"""
Orchestration: site profile (no login) and full NISAR investigation (server token).
"""
from __future__ import annotations

import base64
import io
import threading
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import box as sbox, mapping
from shapely.ops import unary_union

from . import attribution, config, context, nisar
from . import physics
from .changedetect import detect_changes, estimate_enl, to_db

# reads wait on the network, not the CPU: 10 files at a time is ~2.5x faster than 4
WORKERS = 10
VALIDATION = json.loads((Path(__file__).parent / "data" / "validation.json").read_text())
LC_COLORS = {10: (0, 100, 0), 20: (255, 187, 34), 30: (255, 255, 76), 40: (240, 150, 255), 50: (250, 0, 0),
             60: (180, 180, 180), 70: (240, 240, 240), 80: (0, 100, 200), 90: (0, 150, 160),
             95: (0, 207, 117), 100: (250, 230, 160)}


# ------------------------------------------------------------------ helpers

def png_url(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def stretch(a, lo=2, hi=98):
    v = a[np.isfinite(a)]
    if v.size == 0:
        return np.zeros_like(a)
    p0, p1 = np.percentile(v, [lo, hi])
    return np.clip((a - p0) / max(p1 - p0, 1e-6), 0, 1)


def pix_lonlat(grid, rows, cols):
    s = grid["spacing"]
    xs = grid["x0"] + (np.asarray(cols) + 0.5) * s
    ys = grid["y1"] - (np.asarray(rows) + 0.5) * s
    return Transformer.from_crs(grid["epsg"], 4326, always_xy=True).transform(xs, ys)


def clean(o):
    """JSON-safe: numpy -> Python, NaN/inf -> None (browsers reject NaN in JSON)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _f(v, nd=2):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


# ------------------------------------------------------------------ site profile (no login)

def site_profile(lat: float, lon: float, half_km: float = 3.0) -> dict:
    grid = context.make_grid(lat, lon, half_km, 30.0)
    with ThreadPoolExecutor(4) as ex:
        f_ter = ex.submit(context.terrain, grid)
        f_lc = ex.submit(context.landcover, grid)
        f_rain = ex.submit(context.rainfall, round(lat, 3), round(lon, 3))
        bounds, corners = context.grid_bounds_lonlat(grid)
        f_place = ex.submit(context.reverse, round(lat, 3), round(lon, 3))
        ter, lc = f_ter.result(), f_lc.result()
        try:
            rain = f_rain.result()
        except Exception as e:
            rain = {"error": str(e)}
        place = f_place.result()
    h, w = grid["shape"]
    cr, cc = h // 2, w // 2
    elev, slope = ter["elevation"], ter["slope"]
    vals, counts = np.unique(lc[lc > 0], return_counts=True)
    cover = sorted([{"code": int(v), "name": context.WORLDCOVER.get(int(v), str(v)), "fraction": round(c / counts.sum(), 3)}
                    for v, c in zip(vals, counts)], key=lambda d: -d["fraction"]) if counts.size else []
    steep = float(np.nanmean(slope > 30)) if np.isfinite(slope).any() else None
    # hillshade + landcover overlays
    hs = _hillshade(elev, grid["spacing"])
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = (hs[..., None] * 255).astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(elev), 150, 0)
    lcrgba = np.zeros((h, w, 4), np.uint8)
    for code, col in LC_COLORS.items():
        m = lc == code
        lcrgba[m, :3] = col
        lcrgba[m, 3] = 170
    rain_total = rain_30 = None
    if rain.get("mm"):
        mm = [v for v in rain["mm"] if v is not None]
        rain_total = round(sum(mm), 1)
        rain_30 = context.rain_before(rain, rain["dates"][-1], 30)
    point = {"elevation_m": _f(elev[cr, cc], 0), "slope_deg": _f(slope[cr, cc], 1),
             "landcover": context.WORLDCOVER.get(int(lc[cr, cc]), "unknown") if lc[cr, cc] else "unknown"}
    return {
        "lat": lat, "lon": lon, "half_km": half_km, "place": place, "point": point,
        "terrain": {"elev_min": _f(np.nanmin(elev), 0), "elev_max": _f(np.nanmax(elev), 0),
                    "elev_mean": _f(np.nanmean(elev), 0), "slope_mean": _f(np.nanmean(slope), 1),
                    "slope_p90": _f(np.nanpercentile(slope, 90), 1) if np.isfinite(slope).any() else None,
                    "steep_fraction": _f(steep, 3), "source": "Copernicus DEM GLO-30 (ESA)"},
        "landcover": {"classes": cover, "source": "ESA WorldCover 10 m 2021"},
        "rain": rain | {"total_mm": rain_total, "last30_mm": rain_30},
        "overlays": {"corners": corners, "hillshade": png_url(rgba), "landcover": png_url(lcrgba)},
    }


def osm_profile(lat: float, lon: float, half_km: float = 3.0) -> dict:
    """Roads, rivers and buildings around the place (OpenStreetMap; can be slow)."""
    grid = context.make_grid(lat, lon, half_km, 30.0)
    bounds, _ = context.grid_bounds_lonlat(grid)
    osm = context.osm(round(bounds[1], 4), round(bounds[0], 4), round(bounds[3], 4), round(bounds[2], 4))
    road_len = 0.0
    tr = Transformer.from_crs(4326, grid["epsg"], always_xy=True)
    for r in osm.get("roads", []):
        if len(r["coords"]) > 1:
            xs, ys = tr.transform([c[0] for c in r["coords"]], [c[1] for c in r["coords"]])
            road_len += float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
    return {"road_km": round(road_len / 1000, 2), "rivers": len(osm.get("rivers", [])),
            "buildings": osm.get("buildings"),
            "nearest_road": context.nearest(osm.get("roads", []), lon, lat, grid["epsg"]),
            "nearest_river": context.nearest(osm.get("rivers", []), lon, lat, grid["epsg"]),
            "roads": [r["coords"] for r in osm.get("roads", [])][:600],
            "river_lines": [r["coords"] for r in osm.get("rivers", [])][:300],
            "error": osm.get("error"), "source": osm.get("source", "OpenStreetMap")}


def _hillshade(dem, spacing, az=315, alt=45):
    gy, gx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem) if np.isfinite(dem).any() else 0), spacing)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(az), np.radians(alt)
    hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


# ------------------------------------------------------------------ investigation (server token)

def _regrid(prod, grid):
    """Average a product's pixels into the analysis grid cells (any source spacing)."""
    h, w = grid["shape"]
    s = grid["spacing"]
    cols = np.floor((prod["x"] - grid["x0"]) / s).astype(int)
    rows = np.floor((grid["y1"] - prod["y"]) / s).astype(int)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    ok = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
    flat = (rr * w + cc)[ok]
    out = {}
    for ch, a in prod["channels"].items():
        v = a[ok]
        good = np.isfinite(v)
        ssum = np.bincount(flat[good], weights=v[good], minlength=h * w)
        cnt = np.bincount(flat[good], minlength=h * w)
        m = np.where(cnt > 0, ssum / np.maximum(cnt, 1), np.nan).reshape(h, w)
        out[ch] = m
    return out


def _gunw_dates(name):
    d = re.findall(r"_(\d{8})T\d{6}", name)
    return (f"{d[0][:4]}-{d[0][4:6]}-{d[0][6:]}", f"{d[2][:4]}-{d[2][4:6]}-{d[2][6:]}") if len(d) >= 4 else (None, None)


def investigate(lat: float, lon: float, half_km: float = 3.0, progress=lambda p, m, eta=None: None,
                layer_key: str | None = None) -> dict:
    import time as _t
    t_start = _t.time()
    progress(0.02, "Finding NISAR passes over this place")
    cov = nisar.coverage(lat, lon, half_km)
    track = nisar.choose_track(cov)
    if track is None:
        raise RuntimeError("No NISAR track fully covers this area with at least 4 dates yet.")
    acq = track["acquisitions"][-config.MAX_DATES:]
    pols = [p for p in ("HH", "HV") if p in track["pols_always"]]
    gcov_all = [it for it in nisar.search("GCOV", nisar.window_bbox(lat, lon, half_km))
                if it["track"] == track["track"] and it["date"] in {a["date"] for a in acq}]
    # Forecast-orbit products (accuracy "F") can be misregistered on steep terrain, which
    # creates false changes; leave them out of change testing and say so.
    excluded = [{"date": it["date"], "reason": "forecast orbit (least accurate geolocation); waiting for the reprocessed product"}
                for it in gcov_all if it.get("orbit") == "F"]
    gcov_items = sorted([it for it in gcov_all if it.get("orbit") != "F"], key=lambda d: d["date"])
    n = len(gcov_items)
    # first-time estimate from measured runs with 8 worker processes (~3 s per file overall)
    # a second, opposite-direction track doubles the interferogram reading time
    n_geoms = 2 if any(t["direction"] != track["direction"] and t["min_coverage"] >= 0.95 and t["gunw_pairs"] >= 3
                       for t in cov["tracks"]) else 1
    progress(0.05, f"Reading {n} radar images from track {track['track']}", n * 9 * (1 + 1.1 * n_geoms) + 60)
    prods = [None] * n
    done = [0]

    t_read = _t.time()
    # Terrain, land cover, rain and roads don't depend on the radar: load them meanwhile.
    ctx_pool = ThreadPoolExecutor(4)
    grid0 = context.make_grid(lat, lon, half_km, 20.0, context.utm_epsg(lat, lon))
    b0, _ = context.grid_bounds_lonlat(grid0)
    f_ter = ctx_pool.submit(context.terrain, grid0)
    f_lc = ctx_pool.submit(context.landcover, grid0)
    f_rain = ctx_pool.submit(context.rainfall, round(lat, 3), round(lon, 3))
    f_osm = ctx_pool.submit(context.osm, round(b0[1] - 0.01, 4), round(b0[0] - 0.01, 4), round(b0[3] + 0.01, 4), round(b0[2] + 0.01, 4))
    t_est = n * 9 * (1 + 1.1 * n_geoms) + 60  # whole-run estimate from measured runs (8 worker processes)

    def loaded(i):
        done[0] += 1
        per = (_t.time() - t_read) / done[0]
        # measured on real runs: reading the interferograms takes about 1.1x the image phase
        measured = per * (n - done[0]) + per * n * 1.1 * n_geoms + 10
        modelled = max(30.0, t_est - (_t.time() - t_start))
        eta = modelled if done[0] < n / 2 else measured
        progress(0.05 + 0.55 * done[0] / n, f"Read image {done[0]} of {n} ({gcov_items[i]['date']})", eta)

    prods = nisar.read_parallel("gcov", [(it["url"], lat, lon, half_km, pols) for it in gcov_items], loaded)
    spacing = max(20.0, max(p["spacing"] for p in prods))
    grid = context.make_grid(lat, lon, half_km, spacing, prods[0]["epsg"])
    stacks = {ch: np.stack([_regrid(p, grid).get(ch, np.full(grid["shape"], np.nan)) for p in prods]) for ch in pols}
    dates = [it["date"] for it in gcov_items]
    progress(0.62, "Removing radar shadow, layover and the regional seasonal swing")
    bounds, corners = context.grid_bounds_lonlat(grid)
    same = grid["epsg"] == grid0["epsg"] and grid["spacing"] == grid0["spacing"] and grid["transform"] == grid0["transform"]
    ter = f_ter.result() if same else context.terrain(grid)
    lc = f_lc.result() if same else context.landcover(grid)
    osm = f_osm.result()
    try:
        rain = f_rain.result()
    except Exception:
        rain = None
    ctx_pool.shutdown(wait=False)

    # 1) radar geometry: layover/shadow pixels carry no usable signal -> left out of the test
    geom = physics.radar_geometry(ter["elevation"], grid["spacing"], physics.los_vector(track["direction"]))
    bad_geom = geom["layover"] | geom["shadow"]
    obs = {ch: np.where(bad_geom[None], np.nan, a) for ch, a in stacks.items()}
    # 2) seasonal detrending per land-cover class BEFORE testing (monsoon -> dry shifts whole classes)
    detr, seasonal = physics.seasonal_detrend(obs, lc)
    # 3) HH-HV correlation measured in this scene keeps the combined test calibrated
    r_corr = physics.channel_corr(detr)
    progress(0.66, "Testing every pixel for statistically significant change")
    enl = estimate_enl(detr["HH"][0])
    det = detect_changes(detr, enl=enl, alpha=0.01, chan_corr=r_corr)
    stacks = detr

    read_time = _t.time() - t_read
    progress(0.76, "Measuring ground motion from interferograms", read_time * 1.1 * n_geoms + 8)
    pace = {"per": None}  # measured seconds per interferogram, shared by both orbits

    def gunw_progress(p0, p1, label, remaining_geoms):
        def cb(done, total):
            elapsed = _t.time() - cb.t0
            per = pace["per"] = elapsed / max(done, 1)
            if done < total / 2:  # first files include connection start-up: use the measured ~35 s per orbit
                eta = max(10.0, 35 - elapsed) + 35 * remaining_geoms + 6
            else:
                eta = per * (total - done) + per * total * remaining_geoms + 6
            progress(p0 + (p1 - p0) * done / total, f"{label}: interferogram {done} of {total}", eta)
        cb.t0 = _t.time()
        return cb

    motion = None
    try:
        motion = _motion(lat, lon, half_km, track["track"], dates[0], dates[-1], ter, grid,
                         on_pair=gunw_progress(0.76, 0.84 if n_geoms == 2 else 0.88, "Ground motion", n_geoms - 1))
    except Exception as e:  # keep the rest of the analysis
        motion = {"error": str(e)}

    # second viewing geometry: a full-coverage track flying the opposite direction
    motion2d = None
    other = next((t for t in cov["tracks"] if t["direction"] != track["direction"] and t["min_coverage"] >= 0.95
                  and t["gunw_pairs"] >= 3), None)
    if motion and "_vel" in motion and other:
        progress(0.84, f"Measuring motion from the other orbit direction (track {other['track']})",
                 41)
        try:
            m2 = _motion(lat, lon, half_km, other["track"], dates[0], dates[-1], ter, grid,
                         on_pair=gunw_progress(0.84, 0.88, "Other orbit direction", 0))
            motion2d = _decompose(motion, m2) if "_vel" in m2 else {"error": m2.get("error")}
        except Exception as e:
            motion2d = {"error": str(e)}

    progress(0.88, "Grouping changes into events and ranking explanations")
    events = _events(det, stacks, dates, grid, ter, lc, osm, rain, motion, track, motion2d, geom)
    progress(0.96, "Drawing map layers")
    overlays = _overlays(det, stacks, dates, grid)
    valid = det["change_count"] >= 0
    changed = det["change_count"] > 0
    per_date = [int(det["change_flags"][i][valid].sum()) for i in range(len(dates))]
    return clean({
        "lat": lat, "lon": lon, "half_km": half_km,
        "track": {k: track[k] for k in ("track", "direction", "n_dates", "pols_always", "next_expected_pass")},
        "dates": dates,
        "maturity": [it["maturity"] for it in gcov_items],
        "orbit_accuracy": [it.get("orbit") for it in gcov_items],
        "excluded_dates": excluded,
        "modes": [it["mode"] for it in gcov_items],
        "pixel_m": spacing, "enl": round(enl, 1), "alpha": 0.01,
        "changed_fraction": round(float(changed[valid].mean()), 4) if valid.any() else None,
        "changes_per_date_px": per_date,
        "events": events,
        "motion": None if motion is None else {k: v for k, v in motion.items() if not k.startswith("_")},
        "motion2d": None if motion2d is None else {k: v for k, v in motion2d.items() if not k.startswith("_")},
        "overlays": overlays | {"corners": corners} | ({"layers": _write_layers(layer_key, obs, dates)} if layer_key else {}),
        "seasonal": [{"code": c, "name": context.WORLDCOVER.get(c, str(c)), **v} for c, v in seasonal.items()],
        "chan_corr": r_corr, "detrended": True,
        "masked_fraction": round(float(bad_geom.mean()), 3),
        "validation": VALIDATION,
        "read_seconds": [p["seconds"] for p in prods],
    })


def _motion(lat, lon, half_km, track, d0, d1, ter, grid, on_pair=None):
    items = [it for it in nisar.search("GUNW", nisar.window_bbox(lat, lon, half_km))
             if it["track"] == track and it.get("orbit") != "F"]
    pairs = []
    for it in items:
        a, b = _gunw_dates(it["name"])
        if a and b and a >= d0 and b <= d1:
            pairs.append((a, b, it))
    # Keep a non-overlapping chain (shortest pair first at each start date) so no interval is counted twice,
    # even if multi-interval interferograms are ever published alongside the 12-day ones.
    pairs.sort(key=lambda p: (p[0], p[1]))
    chain, last_end = [], ""
    for p in pairs:
        if p[0] >= last_end:
            chain.append(p)
            last_end = p[1]
    pairs = chain
    if not pairs:
        return {"error": "No interferograms inside the analysed date range yet."}
    done = [0]
    lock = threading.Lock()

    first_url = pairs[0][2]["url"]

    def read_done(_i):
        with lock:
            done[0] += 1
            if on_pair:
                on_pair(done[0], len(pairs))

    reads = nisar.read_parallel("gunw", [(p[2]["url"], lat, lon, half_km, p[2]["url"] == first_url) for p in pairs], read_done)
    shape = reads[0]["phase"].shape
    cum = np.zeros(shape, np.float32)
    valid_days = np.zeros(shape, np.float32)
    series, coh_series = [], []
    cums = []
    for (a, b, it), r in zip(pairs, reads):
        if r["phase"].shape != shape:
            continue
        ph = r["phase"].copy()
        if "iono" in r:
            ph = ph - r["iono"]
        good = np.isfinite(ph)
        if "coherence" in r:
            good &= r["coherence"] >= 0.3
        if "components" in r:
            cc = r["components"]
            good &= cc > 0
            # Each connected component has its own unknown 2*pi offset; only the largest
            # component in the window can be referenced consistently.
            ids, counts = np.unique(cc[good], return_counts=True)
            if ids.size:
                good &= cc == ids[np.argmax(counts)]
        ref = np.nanmedian(ph[good]) if good.any() else 0.0
        los = (ph - ref) * nisar.LAMBDA_L / (4 * np.pi) * 1000.0  # mm, relative to window median
        los[~good] = np.nan
        # a 12-day step beyond half a phase cycle (~60 mm) is an unwrapping error, not motion
        los[np.abs(los) > nisar.LAMBDA_L / 4 * 1000] = np.nan
        days = (date.fromisoformat(b) - date.fromisoformat(a)).days
        ok = np.isfinite(los)
        cum = cum + np.where(ok, los, 0.0)
        valid_days = valid_days + np.where(ok, days, 0)
        cums.append(np.where(ok, cum, np.nan))
        series.append({"from": a, "to": b, "days": days})
        coh_series.append(r.get("coherence"))
    x, y = reads[0]["x"], reads[0]["y"]
    dx = float(abs(x[1] - x[0]))
    mgrid = {"epsg": reads[0]["epsg"], "x": x, "y": y, "dx": dx}
    total_days = sum(s["days"] for s in series) or 1
    # velocity from each pixel's OWN valid days; pixels valid for under half the time are not estimated
    vel = np.where(valid_days >= 0.5 * total_days, cum / np.maximum(valid_days, 1) * 12, np.nan)  # mm per 12 days
    # troposphere check: does cumulative motion simply follow elevation?
    tropo = None
    try:
        tr = Transformer.from_crs(mgrid["epsg"], grid["epsg"], always_xy=True)
        X, Y = np.meshgrid(x, y)
        gx, gy = tr.transform(X, Y)
        cols = np.clip(((gx - grid["x0"]) / grid["spacing"]).astype(int), 0, grid["shape"][1] - 1)
        rows = np.clip(((grid["y1"] - gy) / grid["spacing"]).astype(int), 0, grid["shape"][0] - 1)
        el = ter["elevation"][rows, cols]
        ok = np.isfinite(el) & np.isfinite(cums[-1])
        if ok.sum() > 50:
            tropo = float(abs(np.corrcoef(el[ok], cums[-1][ok])[0, 1]))
    except Exception:
        pass
    lim = np.nanpercentile(np.abs(vel), 98) if np.isfinite(vel).any() else 1
    rgba = _diverging(vel, max(lim, 2.0))
    x0, y1 = x[0] - dx / 2, y[0] + dx / 2
    trb = Transformer.from_crs(mgrid["epsg"], 4326, always_xy=True)
    xe, ye = x0 + len(x) * dx, y1 - len(y) * dx
    corners = [trb.transform(x0, y1), trb.transform(xe, y1), trb.transform(xe, ye), trb.transform(x0, ye)]
    return {"pairs": series, "n_pairs": len(series), "velocity_png": png_url(rgba), "velocity_limit_mm": round(float(lim), 1),
            "corners": corners, "elevation_correlation": None if tropo is None else round(tropo, 2),
            "phase_note": reads[0].get("phase_attrs"),
            "convention": "Line-of-sight change relative to the median of this window. Ionosphere screen removed; "
                          "troposphere not corrected. Check the GUNW product specification for the sign convention.",
            "los": reads[0].get("los"), "track": track,
            "_grid": mgrid, "_cum": cums, "_coh": coh_series, "_vel": vel}


def _decompose(m1, m2):
    """
    Combine two viewing geometries (e.g. northbound and southbound) into vertical and
    east-west motion. LOS change = s * (E*e + U*u) with (e, u) the target->sensor unit
    vector; north is ignored (NISAR's near-polar orbit is almost blind to it). The overall
    sign s of NISAR's phase convention is not stated in the product specification, so
    only magnitudes and the vertical/horizontal split are reported as reliable.
    """
    l1, l2 = m1.get("los"), m2.get("los")
    if not l1 or not l2:
        return {"error": "line-of-sight geometry missing from the products"}
    A = np.array([[l1["e"], l1["u"]], [l2["e"], l2["u"]]])
    if abs(np.linalg.det(A)) < 0.1:
        return {"error": "the two orbits look from too similar directions to separate the motion"}
    g1, g2 = m1["_grid"], m2["_grid"]
    X, Y = np.meshgrid(g1["x"], g1["y"])
    if g2["epsg"] != g1["epsg"]:
        X, Y = Transformer.from_crs(g1["epsg"], g2["epsg"], always_xy=True).transform(X, Y)
    ci = np.round((X - g2["x"][0]) / g2["dx"]).astype(int)
    ri = np.round((g2["y"][0] - Y) / g2["dx"]).astype(int)
    ok = (ci >= 0) & (ci < len(g2["x"])) & (ri >= 0) & (ri < len(g2["y"]))
    v2 = np.full(X.shape, np.nan)
    v2[ok] = m2["_vel"][ri[ok], ci[ok]]
    inv = np.linalg.inv(A)
    E = inv[0, 0] * m1["_vel"] + inv[0, 1] * v2
    U = inv[1, 0] * m1["_vel"] + inv[1, 1] * v2
    mag = np.hypot(E, U)
    lim = float(np.nanpercentile(mag, 98)) if np.isfinite(mag).any() else 1.0
    t = np.clip(np.nan_to_num(mag / max(lim, 1.0)), 0, 1)
    rgba = np.zeros(mag.shape + (4,), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 253, (231 - 120 * t).astype(np.uint8), (37 + 60 * t).astype(np.uint8)
    rgba[..., 3] = np.where(np.isfinite(mag), (30 + 210 * t).astype(np.uint8), 0)
    valid = np.isfinite(U) & np.isfinite(E)
    vert_share = float(np.nanmedian(np.abs(U[valid]) / (np.abs(U[valid]) + np.abs(E[valid]) + 1e-6))) if valid.any() else None
    return {"tracks": [m1["track"], m2["track"]], "pairs": [m1["n_pairs"], m2["n_pairs"]],
            "magnitude_png": png_url(rgba), "magnitude_limit_mm": round(lim, 1), "corners": m1["corners"],
            "median_vertical_share": None if vert_share is None else round(vert_share, 2),
            "sign_confirmed": False,
            "note": "Size and vertical/east-west split are reliable; whether vertical motion is up or down is not "
                    "confirmed, because NISAR's product specification does not state the phase sign convention.",
            "_U": U, "_E": E}


def _diverging(v, lim, min_alpha=40):
    t = np.clip(np.nan_to_num(v / lim), -1, 1)
    rgba = np.zeros(v.shape + (4,), np.uint8)
    neg = t < 0
    a = np.abs(t)
    rgba[..., 0] = np.where(neg, 75, 228)
    rgba[..., 1] = np.where(neg, 123, 111)
    rgba[..., 2] = np.where(neg, 229, 176)
    rgba[..., 3] = np.where(np.isfinite(v), (min_alpha + (240 - min_alpha) * a).astype(np.uint8), 0)
    return rgba


def _events(det, stacks, dates, grid, ter, lc, osm, rain, motion, track, motion2d=None, geom=None, min_px=13, max_events=25):
    # rasterize roads and rivers once for the downhill-path test
    to_ll = Transformer.from_crs(4326, grid["epsg"], always_xy=True)

    def to_rc(lo, la):
        x, y = to_ll.transform(lo, la)
        return (grid["y1"] - np.asarray(y)) / grid["spacing"], (np.asarray(x) - grid["x0"]) / grid["spacing"]
    river_mask = physics.rasterize_lines(osm.get("rivers", []) and [r["coords"] for r in osm["rivers"]] or [], to_rc, grid["shape"])
    road_mask = physics.rasterize_lines(osm.get("roads", []) and [r["coords"] for r in osm["roads"]] or [], to_rc, grid["shape"])
    river_mask = ndimage.binary_dilation(river_mask, iterations=2)
    road_mask = ndimage.binary_dilation(road_mask, iterations=1)
    changed = det["change_count"] > 0
    changed = ndimage.binary_opening(changed, np.ones((2, 2)))
    lab, nlab = ndimage.label(changed, np.ones((3, 3)))
    if nlab == 0:
        return []
    sizes = ndimage.sum(changed, lab, range(1, nlab + 1))
    order = np.argsort(-sizes)
    s = grid["spacing"]
    out = []
    db = {ch: to_db(a) for ch, a in stacks.items()}
    dil = ndimage.binary_dilation(changed, iterations=3)  # keep other changes out of the reference ring
    for rank, li in enumerate(order[:max_events * 2]):
        if sizes[li] < min_px or len(out) >= max_events:
            break
        m = lab == li + 1
        rows, cols = np.where(m)
        firsts = det["first_change"][m]
        idx = int(np.bincount(firsts[firsts >= 0]).argmax())
        n_changes = int(np.round(np.median(det["change_count"][m])))
        lasts = det["last_change"][m]
        nxt = int(np.bincount(lasts[lasts >= 0]).argmax()) if n_changes > 1 else None
        ring = ndimage.binary_dilation(m, iterations=15) & ~ndimage.binary_dilation(m, iterations=5) & ~dil
        series, deltas = {}, {}
        for ch, a in db.items():
            ev = np.nanmean(a[:, m], axis=1)
            series[ch] = [_f(v) for v in ev]
            ref = np.nanmean(a[:, ring], axis=1) if ring.any() else np.full(len(dates), np.nan)
            series[ch + "_surroundings"] = [_f(v) for v in ref]
            end = nxt if nxt and nxt > idx else len(dates)
            before = np.nanmean(ev[:idx]) if idx > 0 else np.nan
            after = np.nanmean(ev[idx:end])
            deltas[ch] = after - before
            if ring.any():
                deltas[ch + "_rel"] = deltas[ch] - (np.nanmean(ref[idx:end]) - np.nanmean(ref[:idx]) if idx > 0 else 0)
        # persisted (False) only if at least 2 later images confirm it; None if too close to the end
        transient = False if (n_changes == 1 and idx <= len(dates) - 3) else None
        if nxt is not None and nxt > idx and "HH" in db:
            ev = np.nanmean(db["HH"][:, m], axis=1)
            after2 = np.nanmean(ev[nxt:])
            transient = bool(np.sign(after2 - np.nanmean(ev[idx:nxt])) == -np.sign(deltas["HH"]))
        lons, lats = pix_lonlat(grid, rows, cols)
        clon, clat = float(np.mean(lons)), float(np.mean(lats))
        poly = unary_union([sbox(c * s, -r * s, (c + 1) * s, -(r + 1) * s) for r, c in zip(rows, cols)]).simplify(s / 2)
        tr = Transformer.from_crs(grid["epsg"], 4326, always_xy=True)

        def to_ll(geom):
            g = mapping(geom)

            def conv(ring_):
                return [list(tr.transform(grid["x0"] + x, grid["y1"] + y)) for x, y in ring_]
            if g["type"] == "Polygon":
                return {"type": "Polygon", "coordinates": [conv(r_) for r_ in g["coordinates"]]}
            return {"type": "MultiPolygon", "coordinates": [[conv(r_) for r_ in p] for p in g["coordinates"]]}

        lc_vals = lc[m][lc[m] > 0]
        lc_name = context.WORLDCOVER.get(int(np.bincount(lc_vals).argmax()), "unknown") if lc_vals.size else "unknown"
        fp = {
            "id": f"EV-{len(out) + 1:02d}", "date_first": dates[idx], "date_index": idx,
            "date_next_change": dates[nxt] if nxt is not None and nxt < len(dates) else None,
            "n_changes": n_changes, "transient": transient,
            "latest_only": idx >= len(dates) - 1,
            "area_ha": round(float(m.sum() * s * s / 1e4), 2), "centroid": [round(clon, 6), round(clat, 6)],
            "dHH_db": _f(deltas.get("HH")), "dHV_db": _f(deltas.get("HV")), "dVV_db": None, "dVH_db": None,
            "relative_to_surroundings_db": _f(deltas.get("HH_rel")),
            "slope_deg": _f(np.nanmean(ter["slope"][m]), 1), "elev_m": _f(np.nanmean(ter["elevation"][m]), 0),
            "aspect_deg": _f(float(np.degrees(np.arctan2(np.nanmean(np.sin(np.radians(ter["aspect"][m]))), np.nanmean(np.cos(np.radians(ter["aspect"][m]))))) % 360), 0),
            "facing_radar": None if geom is None else {1: "towards the radar", -1: "away from the radar", 0: "side-on to the radar"}[int(np.sign(np.round(np.nanmean(geom["facing"][m]))))],
            "local_incidence_deg": None if geom is None else _f(np.nanmean(geom["local_incidence"][m]), 0),
            "shadow_fraction": None if geom is None else _f(float(ndimage.binary_dilation(geom["shadow"], iterations=2)[m].mean()), 2),
            "layover_fraction": None if geom is None else _f(float(ndimage.binary_dilation(geom["layover"], iterations=2)[m].mean()), 2),
            "landcover": lc_name,
            "rain_30d_mm": context.rain_before(rain, dates[idx]) if rain else None,
            "nearest_road": context.nearest(osm.get("roads", []), clon, clat, grid["epsg"]),
            "nearest_river": context.nearest(osm.get("rivers", []), clon, clat, grid["epsg"]),
            "min_p": _f(float(np.min(det["min_p"][m])), 6),
            "series": {"dates": dates, **series},
            "geometry": to_ll(poly),
        }
        _event_downhill(fp, ter, grid, rows, cols, m, river_mask, road_mask, osm)
        _event_motion(fp, motion, grid, rows, cols)
        _event_motion2d(fp, motion, motion2d, grid, rows, cols)
        valid_frac = float(np.isfinite(stacks["HH"][:, m]).mean())
        fp["quality"] = {"score": round(min(1.0, valid_frac), 2),
                         "summary": f"{valid_frac:.0%} valid samples; {len(dates)} dates; ENL {det['enl']:.0f}"}
        fp["hypotheses"] = attribution.hypotheses(fp)
        fp["priority"] = attribution.priority(fp)
        fp["relationships"] = _relationships(fp)
        out.append(fp)
    out.sort(key=lambda e: e["priority"]["score"], reverse=True)
    return out


def _event_motion(fp, motion, grid, rows, cols):
    fp.update({"los_rate_mm_per_12d": None, "los_consistent_pairs": None, "los_acceleration": None,
               "coherence_change": None, "motion_tracks_elevation": None, "los_series": None})
    if not motion or "_cum" not in motion:
        return
    mg = motion["_grid"]
    s = grid["spacing"]
    xs = grid["x0"] + (cols + 0.5) * s
    ys = grid["y1"] - (rows + 0.5) * s
    if mg["epsg"] != grid["epsg"]:
        xs, ys = Transformer.from_crs(grid["epsg"], mg["epsg"], always_xy=True).transform(xs, ys)
    ci = np.clip(np.round((xs - mg["x"][0]) / mg["dx"]).astype(int), 0, len(mg["x"]) - 1)
    ri = np.clip(np.round((mg["y"][0] - ys) / mg["dx"]).astype(int), 0, len(mg["y"]) - 1)
    cum = [float(np.nanmean(c[ri, ci])) if np.isfinite(c[ri, ci]).any() else np.nan for c in motion["_cum"]]
    steps = np.diff([0.0] + cum)
    # A step near a whole phase cycle (lambda/2 = 119 mm) is an unwrapping error, not motion.
    cycle = nisar.LAMBDA_L / 2 * 1000
    bad = np.isfinite(steps) & (np.abs(steps) > cycle / 2)
    fp["los_unwrap_suspect_pairs"] = int(bad.sum())
    steps = np.where(bad, np.nan, steps)
    cum = list(np.nancumsum(np.nan_to_num(steps, nan=0.0)))
    days = [p["days"] for p in motion["pairs"]]
    rates = [st / d * 12 if np.isfinite(st) else np.nan for st, d in zip(steps, days)]
    good = [r for r in rates if np.isfinite(r)]
    if good:
        fp["los_rate_mm_per_12d"] = _f(np.mean(good[-3:]), 1)
        sign = np.sign(np.mean(good))
        fp["los_consistent_pairs"] = int(sum(1 for r in good if np.sign(r) == sign and abs(r) > 1))
        if len(good) >= 5:
            fp["los_acceleration"] = _f(abs(np.mean(good[-3:])) - abs(np.mean(good[:-3])), 1)
    fp["los_series"] = {"to": [p["to"] for p in motion["pairs"]], "mm": [_f(c, 1) for c in cum]}
    cohs = [float(np.nanmean(c[ri, ci])) for c in motion["_coh"] if c is not None]
    if len(cohs) >= 2:
        k = max(1, len(cohs) // 2)
        fp["coherence_change"] = _f(np.mean(cohs[k:]) - np.mean(cohs[:k]), 2)
        fp["coherence_series"] = [_f(c, 2) for c in cohs]
    ec = motion.get("elevation_correlation")
    fp["motion_tracks_elevation"] = None if ec is None else ec > 0.6


def _event_motion2d(fp, motion, m2d, grid, rows, cols):
    fp.update({"motion_vertical_abs": None, "motion_eastwest_abs": None, "motion_mostly": None})
    if not m2d or "_U" not in m2d or not motion or "_grid" not in motion:
        return
    mg = motion["_grid"]
    s = grid["spacing"]
    xs = grid["x0"] + (cols + 0.5) * s
    ys = grid["y1"] - (rows + 0.5) * s
    if mg["epsg"] != grid["epsg"]:
        xs, ys = Transformer.from_crs(grid["epsg"], mg["epsg"], always_xy=True).transform(xs, ys)
    ci = np.clip(np.round((xs - mg["x"][0]) / mg["dx"]).astype(int), 0, len(mg["x"]) - 1)
    ri = np.clip(np.round((mg["y"][0] - ys) / mg["dx"]).astype(int), 0, len(mg["y"]) - 1)
    U, E = m2d["_U"][ri, ci], m2d["_E"][ri, ci]
    if not (np.isfinite(U).any() and np.isfinite(E).any()):
        return
    u, e = abs(float(np.nanmean(U))), abs(float(np.nanmean(E)))
    fp["motion_vertical_abs"] = _f(u, 1)
    fp["motion_eastwest_abs"] = _f(e, 1)
    if max(u, e) >= 4:  # ~10 cm/yr; below this, 12-day interferogram noise dominates
        fp["motion_mostly"] = "vertical" if u >= 1.5 * e else "east-west" if e >= 1.5 * u else "mixed"


def _event_downhill(fp, ter, grid, rows, cols, m, river_mask, road_mask, osm):
    """Feature 7 physics: follow the steepest-descent path over the DEM from the event's highest
    point and record which river or road it actually reaches, how far and how far down."""
    elev = ter["elevation"]
    fp["downhill"] = None
    fp["road_above"] = None
    if not np.isfinite(elev[m]).any():
        return
    i = int(np.nanargmax(np.where(m, elev, -np.inf)))
    start = np.unravel_index(i, elev.shape)
    path = physics.downslope_path(elev, start, max_steps=300)
    if len(path) < 3 or not np.isfinite(elev[path[-1]]) or elev[start] - elev[path[-1]] < 3.0:
        return  # flat ground: there is no real downhill for material to travel
    s = grid["spacing"]
    dist = 0.0
    hit = None
    for k in range(1, len(path)):
        (r0, c0), (r1, c1) = path[k - 1], path[k]
        dist += s * (1.414 if (r0 != r1 and c0 != c1) else 1.0)
        if river_mask[r1, c1] and not m[r1, c1]:
            hit = ("river", r1, c1); break
        if road_mask[r1, c1] and not m[r1, c1]:
            hit = ("road", r1, c1); break
    lon_p, lat_p = pix_lonlat(grid, [p[0] for p in path], [p[1] for p in path])
    fp["downhill"] = {"path": [[round(a, 6), round(b, 6)] for a, b in zip(lon_p, lat_p)][:: max(1, len(path) // 60)],
                      "length_m": round(dist), "drop_m": _f(float(elev[start] - elev[path[-1]]), 0),
                      "reaches": None}
    if hit:
        kind, r1, c1 = hit
        lo, la = pix_lonlat(grid, [r1], [c1])
        near = context.nearest(osm.get("rivers" if kind == "river" else "roads", []), float(lo[0]), float(la[0]), grid["epsg"])
        fp["downhill"]["reaches"] = {"kind": kind, "name": (near or {}).get("name"), "along_path_m": round(dist),
                                     "drop_m": _f(float(elev[start] - elev[r1, c1]), 0)}
    # a road ABOVE the change can be undercut by it (slope failure retreating upslope)
    ring = ndimage.binary_dilation(m, iterations=int(200 / s)) & ~m & road_mask
    if ring.any():
        higher = ring & (elev > np.nanmean(elev[m]) + 10)
        if higher.any():
            fp["road_above"] = {"metres_higher": _f(float(np.nanmax(elev[higher]) - np.nanmean(elev[m])), 0)}


def _relationships(fp):
    """Feature 7: physically traced links, stated with the evidence still needed."""
    out = []
    river = fp.get("nearest_river")
    top = fp["hypotheses"][0]["name"] if fp.get("hypotheses") else ""
    dh = fp.get("downhill") or {}
    reach = dh.get("reaches")
    if reach:
        what = reach.get("name") or ("a river or stream" if reach["kind"] == "river" else "a road")
        out.append({"type": f"downhill-to-{reach['kind']}",
                    "text": f"Following the steepest downhill path from this change, material would reach {what} after "
                            f"{reach['along_path_m']} m and a {reach['drop_m']} m drop.",
                    "evidence_needed": ("A change in the river channel downstream on the same or next pass; optical image of a deposit."
                                        if reach["kind"] == "river" else "Road condition reports or a field check where the path meets the road."),
                    "path": dh.get("path")})
    if fp.get("road_above"):
        out.append({"type": "road-above",
                    "text": f"A road runs about {fp['road_above']['metres_higher']} m higher on the slope above this change; "
                            f"slope failures can retreat upslope and undercut it.",
                    "evidence_needed": "Cracks or subsidence on the road shoulder above the marked area."})
    if "flood" in top.lower() and river:
        out.append({"type": "river-flooding",
                    "text": "If this is flooding, it is likely connected to the nearby river channel.",
                    "evidence_needed": "River gauge levels for the same dates; flood extent on the next pass."})
    return out


def _overlays(det, stacks, dates, grid):
    k = len(dates)
    first = det["first_change"]
    h, w = first.shape
    pal = np.array([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], float)
    t = np.clip(first / max(k - 1, 1), 0, 1)
    seg = t * (len(pal) - 1)
    i0 = np.floor(seg).astype(int).clip(0, len(pal) - 2)
    fr = (seg - i0)[..., None]
    col = pal[i0] * (1 - fr) + pal[i0 + 1] * fr
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., :3] = col.astype(np.uint8)
    rgba[..., 3] = np.where(first >= 0, 220, 0)
    # direction of change at the first change date (mean of channels, dB)
    dirs = np.full((h, w), np.nan)
    acc = []
    for ch, a in stacks.items():
        d = to_db(a)
        idx = np.clip(first, 1, k - 1)
        cum = np.nancumsum(np.nan_to_num(d, nan=0), axis=0)
        cnt = np.cumsum(np.isfinite(d), axis=0)
        rr, cc = np.indices(first.shape)
        before = cum[idx - 1, rr, cc] / np.maximum(cnt[idx - 1, rr, cc], 1)
        after = (cum[-1, rr, cc] - cum[idx - 1, rr, cc]) / np.maximum(cnt[-1, rr, cc] - cnt[idx - 1, rr, cc], 1)
        acc.append(after - before)
    dirs = np.where(first >= 0, np.mean(acc, axis=0), np.nan)
    return {
        "change_date": png_url(rgba),
        # changed pixels drawn nearly opaque so they stand out over the dimmed radar
        "change_direction": png_url(_diverging(dirs, 4.0, min_alpha=190)),
        "rgb_first": png_url(_rgb(stacks, 0)), "rgb_last": png_url(_rgb(stacks, k - 1)),
        "date_palette": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
    }


def _write_layers(key, stacks, dates):
    """Per-date 20 m radar layers (false-colour RGB, HH, HV) on ONE fixed stretch, so dates compare fairly."""
    out_dir = config.CACHE_DIR / "layers" / key
    out_dir.mkdir(parents=True, exist_ok=True)
    db = {ch: to_db(a) for ch, a in stacks.items()}
    lim = {ch: np.nanpercentile(v[np.isfinite(v)], [2, 98]) for ch, v in db.items() if np.isfinite(v).any()}
    if "HV" in db:
        ratio = db["HH"] - db["HV"]
        lim["R"] = np.nanpercentile(ratio[np.isfinite(ratio)], [2, 98])

    def norm(a, l):
        return np.clip(np.nan_to_num((a - l[0]) / max(l[1] - l[0], 1e-6)), 0, 1)
    kinds = ["rgb", "hh"] + (["hv"] if "HV" in db else [])
    for i, d in enumerate(dates):
        hh = db["HH"][i]
        alpha = np.where(np.isfinite(hh), 255, 0).astype(np.uint8)
        layers = {"hh": np.stack([norm(hh, lim["HH"])] * 3, -1)}
        if "HV" in db:
            hv = db["HV"][i]
            layers["hv"] = np.stack([norm(hv, lim["HV"])] * 3, -1)
            layers["rgb"] = np.stack([norm(hh, lim["HH"]), norm(hv, lim["HV"]), norm(hh - hv, lim["R"])], -1)
        else:
            layers["rgb"] = layers["hh"]
        for kind, rgb in layers.items():
            rgba = np.dstack([(rgb * 255).astype(np.uint8), alpha])
            Image.fromarray(rgba, "RGBA").save(out_dir / f"{d}_{kind}.png", optimize=True)
    return {"key": key, "dates": dates, "kinds": kinds}


def _rgb(stacks, i):
    hh = to_db(stacks["HH"][i])
    if "HV" in stacks:
        hv = to_db(stacks["HV"][i])
        r, g, b = stretch(hh), stretch(hv), stretch(hh - hv)
    else:
        r = g = b = stretch(hh)
    rgba = np.zeros(hh.shape + (4,), np.uint8)
    r, g, b = (np.nan_to_num(c) for c in (r, g, b))
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = r * 255, g * 255, b * 255
    rgba[..., 3] = np.where(np.isfinite(hh), 255, 0)
    return rgba
