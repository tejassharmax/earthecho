"""
Radar and terrain physics used before and after change detection.

  radar_geometry   local incidence, layover and shadow from DEM slope/aspect + look direction
  seasonal_detrend remove each land-cover class's regional seasonal swing before testing
  channel_corr     measured HH-HV correlation, so the combined test stays calibrated
  downslope_path   D8 steepest-descent path over the DEM (where material from a change would go)
"""
from __future__ import annotations

import numpy as np

EPS = 1e-12


def los_vector(direction: str, incidence_deg: float = 37.0, heading_deg: float | None = None) -> np.ndarray:
    """Target->sensor unit vector (east, north, up) for NISAR's right-looking geometry.
    Nominal headings for a near-polar orbit: ascending ~350 deg, descending ~190 deg."""
    if heading_deg is None:
        heading_deg = 350.0 if direction.startswith("a") else 190.0
    look_az = np.radians(heading_deg + 90.0)          # direction the radar looks (ground range)
    to_sensor = look_az + np.pi                        # horizontal direction from target to sensor
    inc = np.radians(incidence_deg)
    return np.array([np.sin(inc) * np.sin(to_sensor), np.sin(inc) * np.cos(to_sensor), np.cos(inc)])


def radar_geometry(elevation: np.ndarray, spacing: float, los: np.ndarray) -> dict:
    """Local incidence angle, layover and shadow masks on the analysis grid (rows run south)."""
    dem = np.where(np.isfinite(elevation), elevation, np.nanmean(elevation) if np.isfinite(elevation).any() else 0)
    d_row, d_col = np.gradient(dem, spacing)
    dz_de, dz_dn = d_col, -d_row                       # east and north gradients
    normal = np.stack([-dz_de, -dz_dn, np.ones_like(dem)])
    normal /= np.linalg.norm(normal, axis=0)
    cos_local = np.tensordot(los, normal, axes=1)
    local_inc = np.degrees(np.arccos(np.clip(cos_local, -1, 1)))
    inc = np.degrees(np.arccos(los[2]))
    h = los[:2] / max(np.hypot(*los[:2]), EPS)          # horizontal unit vector towards the sensor
    toward = -(dz_de * h[0] + dz_dn * h[1])            # >0: ground descends towards the sensor (faces it)
    slope_toward = np.degrees(np.arctan(toward))
    layover = slope_toward > inc
    shadow = -slope_toward > (90.0 - inc)
    return {"local_incidence": local_inc, "layover": layover, "shadow": shadow,
            "facing": np.where(slope_toward > 10, 1, np.where(slope_toward < -10, -1, 0)), "incidence_deg": inc}


def seasonal_detrend(stacks: dict, landcover: np.ndarray, min_px: int = 200) -> tuple[dict, dict]:
    """
    Divide out each land-cover class's regional seasonal swing (median dB per date minus its
    median over time) before change testing, so monsoon-to-dry shifts that move a whole class
    are not flagged as local change. Returns detrended stacks and the per-class curves.
    A real change covering most of a class would also be removed; the curves are reported so
    that remains visible.
    """
    out = {ch: a.copy() for ch, a in stacks.items()}
    curves: dict = {}
    for code in np.unique(landcover[landcover > 0]):
        cls = landcover == code
        if cls.sum() < min_px:
            continue
        entry = {"pixels": int(cls.sum())}
        for ch, a in stacks.items():
            vals = a[:, cls]
            with np.errstate(all="ignore"):
                med = np.nanmedian(10 * np.log10(np.where(vals > 0, vals, np.nan)), axis=1)
            if not np.isfinite(med).any():
                continue
            shift = med - np.nanmedian(med)
            shift = np.nan_to_num(shift)
            out[ch][:, cls] = a[:, cls] / (10 ** (shift / 10))[:, None]
            entry[ch] = [None if not np.isfinite(v) else round(float(v), 2) for v in med]
        curves[int(code)] = entry
    return out, curves


def channel_corr(stacks: dict) -> float:
    """
    Correlation r between the per-channel test statistics. For speckle from a complex Gaussian
    with HH-HV coherence rho, intensity correlation is |rho|^2 and the chi-square statistics
    correlate by about |rho|^4; we measure the temporal correlation of log-intensities per
    pixel (robust median) and square it. Dual-pol GCOV has no HH-HV off-diagonal term to use.
    """
    if not {"HH", "HV"} <= set(stacks):
        return 0.0
    a = np.log(np.maximum(stacks["HH"], EPS))
    b = np.log(np.maximum(stacks["HV"], EPS))
    ok = np.all(np.isfinite(a) & np.isfinite(b), axis=0)
    if ok.sum() < 100 or a.shape[0] < 4:
        return 0.0
    a = a[:, ok] - a[:, ok].mean(axis=0)
    b = b[:, ok] - b[:, ok].mean(axis=0)
    r = (a * b).sum(axis=0) / np.sqrt((a * a).sum(axis=0) * (b * b).sum(axis=0) + EPS)
    rho = float(np.clip(np.nanmedian(r), 0, 1))
    return round(rho ** 2, 3)


D8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def downslope_path(elevation: np.ndarray, start: tuple[int, int], max_steps: int = 400) -> list[tuple[int, int]]:
    """Steepest-descent (D8) path from start until a pit, the edge, or max_steps."""
    h, w = elevation.shape
    r, c = start
    path = [(r, c)]
    for _ in range(max_steps):
        z = elevation[r, c]
        best, nxt = 0.0, None
        for dr, dc in D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and np.isfinite(elevation[rr, cc]):
                drop = (z - elevation[rr, cc]) / (1.414 if dr and dc else 1.0)
                if drop > best:
                    best, nxt = drop, (rr, cc)
        if nxt is None:
            break
        r, c = nxt
        path.append(nxt)
    return path


def rasterize_lines(lines: list[list[tuple[float, float]]], to_rc, shape: tuple[int, int], step_px: float = 0.5) -> np.ndarray:
    """Mark grid cells touched by lon/lat polylines; to_rc maps (lon, lat) arrays to (row, col)."""
    mask = np.zeros(shape, bool)
    for ln in lines:
        if len(ln) < 2:
            continue
        rows, cols = to_rc(np.array([p[0] for p in ln]), np.array([p[1] for p in ln]))
        for i in range(len(rows) - 1):
            n = int(max(abs(rows[i + 1] - rows[i]), abs(cols[i + 1] - cols[i])) / step_px) + 1
            rr = np.round(np.linspace(rows[i], rows[i + 1], n + 1)).astype(int)
            cc = np.round(np.linspace(cols[i], cols[i + 1], n + 1)).astype(int)
            ok = (rr >= 0) & (rr < shape[0]) & (cc >= 0) & (cc < shape[1])
            mask[rr[ok], cc[ok]] = True
    return mask
