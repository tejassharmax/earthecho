"""
NISAR data access: search (no login), track selection, and window streaming of
GCOV / GUNW HDF5 products (server-side Earthdata token; users never log in).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import date, timedelta

import asf_search as asf
import fsspec
import h5py
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping, shape

from . import config

CAL_START = "2026-06-17"  # first calibrated (PROVISIONAL) acquisitions
DATA_GAPS = [("2026-07-27", "2026-08-10")]
LAMBDA_L = 0.2384  # m, NISAR L-band (1.257 GHz)


# ---------------------------------------------------------------- search (public)

def _rank(name: str):
    proc = 1 if "_PR_" in name else 0
    crid = re.search(r"_P(\d{5})_", name)
    ctr = re.search(r"_(\d{3})$", name)
    return (proc, int(crid.group(1)) if crid else 0, int(ctr.group(1)) if ctr else 0)


def maturity(crid: str | None, url: str) -> str:
    if "BETA" in url:
        return "BETA"
    if "PROVISIONAL" in url:
        return "PROVISIONAL"
    return "VALIDATED" if (crid or "") >= "P05036" else "PROVISIONAL"


def search(level: str, aoi, start: str = CAL_START, max_results: int = 2000) -> list[dict]:
    res = asf.search(dataset="NISAR", processingLevel=level, intersectsWith=aoi.wkt,
                     start=start, maxResults=max_results)
    best: dict = {}
    for g in res:
        p = g.properties
        name = p["sceneName"]
        key = (p["pathNumber"], p["flightDirection"], p["frameNumber"], p["startTime"][:10])
        if key in best and _rank(name) <= _rank(best[key]["name"]):
            continue
        parts = name.split("_")
        best[key] = {
            "name": name, "url": p["url"], "date": p["startTime"][:10], "time": p["startTime"],
            "track": f"{p['pathNumber']:03d}_{p['flightDirection'][0]}_{p['frameNumber']:03d}",
            "direction": p["flightDirection"].lower(), "crid": p.get("crid"),
            "maturity": maturity(p.get("crid"), p["url"]),
            "mode": parts[9] if level == "GCOV" and len(parts) > 9 else parts[10] if len(parts) > 10 else None,
            "coverage": round(shape(g.geometry).intersection(aoi).area / max(aoi.area, 1e-12), 3),
            # product accuracy of orbit/pointing: P precise, M medium, N near-real-time, F forecast
            "orbit": parts[-4] if len(parts) > 4 and parts[-4] in "PMNF" else None,
            "browse": next((b for b in (p.get("browse") or []) if b.endswith("_LATLON_thumbnail.png")), None),
            "footprint": g.geometry,
        }
    return sorted(best.values(), key=lambda d: d["time"])


def window_bbox(lat: float, lon: float, half_km: float):
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * max(math.cos(math.radians(lat)), 0.05))
    return box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def pols_from_mode(mode: str | None) -> list[str]:
    """Mode code e.g. DHDH, QPDH, SHSH, DHNA -> main-band polarizations."""
    m = (mode or "")[:2]
    return {"QP": ["HH", "HV", "VH", "VV"], "DH": ["HH", "HV"], "DV": ["VV", "VH"],
            "SH": ["HH"], "SV": ["VV"]}.get(m, ["HH"])


def coverage(lat: float, lon: float, half_km: float = 3.0) -> dict:
    """Everything NISAR has acquired over this window (no login needed)."""
    aoi = window_bbox(lat, lon, half_km)
    gcov = search("GCOV", aoi)
    gunw = search("GUNW", aoi)
    tracks: dict[str, dict] = {}
    for item in gcov:
        t = tracks.setdefault(item["track"], {"track": item["track"], "direction": item["direction"],
                                              "acquisitions": [], "gunw_pairs": 0})
        # swath footprint of the latest pass on this track (for the orbit-swath map layer)
        try:
            t["footprint"] = mapping(shape(item["footprint"]).simplify(0.01))
        except Exception:
            pass
        t["acquisitions"].append({k: item[k] for k in ("date", "time", "mode", "maturity", "coverage", "name", "browse", "orbit")}
                                 | {"pols": pols_from_mode(item["mode"])})
    for item in gunw:
        if item["track"] in tracks:
            tracks[item["track"]]["gunw_pairs"] += 1
    today = date.today()
    for t in tracks.values():
        acq = t["acquisitions"]
        t["min_coverage"] = min(a["coverage"] for a in acq)
        t["n_dates"] = len(acq)
        t["pols_always"] = sorted(set.intersection(*[set(a["pols"]) for a in acq])) if acq else []
        nxt = date.fromisoformat(acq[-1]["date"]) + timedelta(days=12)
        while nxt <= today:
            nxt += timedelta(days=12)
        t["next_expected_pass"] = nxt.isoformat()
    ordered = sorted(tracks.values(), key=lambda t: (t["min_coverage"] >= 0.95, t["n_dates"]), reverse=True)
    passes = sorted({a["date"] for t in tracks.values() for a in t["acquisitions"]})
    return {
        "lat": lat, "lon": lon, "half_km": half_km,
        "tracks": ordered,
        "n_acquisitions": len(passes),
        "first": passes[0] if passes else None,
        "last": passes[-1] if passes else None,
        "data_gaps": detect_gaps(passes, len(ordered)),
        "next_expected_pass": min((t["next_expected_pass"] for t in ordered), default=None),
        "revisit_days": round(12 / max(len(ordered), 1), 1) if ordered else None,
    }


def detect_gaps(dates: list[str], n_tracks: int) -> list[tuple[str, str]]:
    """Periods with no NISAR pass at this place, found from the timeline itself: a pause longer
    than 1.5x the expected spacing (12 days / number of tracks) and at least a week."""
    if len(dates) < 2 or n_tracks == 0:
        return []
    limit = max(7.0, 1.5 * 12.0 / n_tracks)
    gaps = []
    for a, b in zip(dates, dates[1:]):
        if (date.fromisoformat(b) - date.fromisoformat(a)).days > limit:
            gaps.append((a, b))
    return gaps


def choose_track(cov: dict, min_cov: float = 0.95, min_dates: int = 4) -> dict | None:
    """Full-coverage track with enough dates; dual-pol (HV) wins if it has at least 75% as many dates,
    because cross-pol is what sees vegetation structure."""
    ok = [t for t in cov["tracks"] if t["min_coverage"] >= min_cov and t["n_dates"] >= min_dates and "HH" in t["pols_always"]]
    if not ok:
        return None
    most = max(t["n_dates"] for t in ok)
    return max(ok, key=lambda t: ("HV" in t["pols_always"] and t["n_dates"] >= 0.75 * most, t["n_dates"]))


# ---------------------------------------------------------------- streaming (token)

_session_lock = threading.Lock()
_session = None


def session():
    global _session
    with _session_lock:
        if _session is None:
            if config.EARTHDATA_TOKEN:
                # Plain bearer token: ASF's data servers accept it directly (asf_search's own
                # token pre-check rejects valid Earthdata user tokens, so it is not used here).
                import requests
                s = requests.Session()
                s.headers["Authorization"] = f"Bearer {config.EARTHDATA_TOKEN}"
                s.headers["User-Agent"] = config.USER_AGENT
                _session = s
                return s
            s = asf.ASFSession()
            if config.EARTHDATA_USERNAME and config.EARTHDATA_PASSWORD:
                s.auth_with_creds(config.EARTHDATA_USERNAME, config.EARTHDATA_PASSWORD)
            else:
                raise RuntimeError("No Earthdata credentials configured on the server (see backend/.env.example)")
            _session = s
        return _session


def signed_url(url: str) -> str:
    r = session().get(url, headers={"Range": "bytes=0-0"}, allow_redirects=True, stream=True, timeout=90)
    r.close()
    if r.status_code not in (200, 206) or "urs.earthdata" in r.url:
        raise RuntimeError(f"Earthdata refused access ({r.status_code}); check the server token")
    return r.url


def open_h5(url: str) -> h5py.File:
    f = fsspec.open(signed_url(url), mode="rb", block_size=4 * 2**20, cache_type="blockcache").open()
    return h5py.File(f, "r")


def _epsg(grp: h5py.Group, lat: float, lon: float) -> int:
    for name in ("projection",):
        if name in grp:
            for k, v in grp[name].attrs.items():
                if "epsg" in k.lower():
                    try:
                        return int(np.asarray(v).ravel()[0])
                    except Exception:
                        pass
            try:
                val = int(np.asarray(grp[name][()]).ravel()[0])
                if 1000 < val < 100000:
                    return val
            except Exception:
                pass
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _index_window(coords: np.ndarray, lo: float, hi: float) -> tuple[int, int]:
    idx = np.where((coords >= lo) & (coords <= hi))[0]
    if idx.size == 0:
        raise ValueError("window outside product")
    return int(idx.min()), int(idx.max()) + 1


def _disk_cached(kind):
    """Cache window reads on disk: NISAR products never change once published."""
    import hashlib
    import pickle

    def deco(fn):
        def wrap(url, lat, lon, half_km, *rest):
            key = hashlib.sha1(json.dumps([kind, url, round(lat, 4), round(lon, 4), half_km, list(rest)]).encode()).hexdigest()[:20]
            path = config.CACHE_DIR / "reads" / f"{kind}_{key}.pkl"
            if path.exists():
                return pickle.loads(path.read_bytes())
            out = fn(url, lat, lon, half_km, *rest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pickle.dumps(out))
            return out
        return wrap
    return deco


@_disk_cached("gcov")
def read_gcov(url: str, lat: float, lon: float, half_km: float, pols: list[str]) -> dict:
    t0 = time.time()
    with open_h5(url) as f:
        g = f["/science/LSAR/GCOV/grids/frequencyA"]
        epsg = _epsg(g, lat, lon)
        tr = Transformer.from_crs(4326, epsg, always_xy=True)
        cx, cy = tr.transform(lon, lat)
        half = half_km * 1000
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]
        ix0, ix1 = _index_window(x, cx - half, cx + half)
        iy0, iy1 = _index_window(y, cy - half, cy + half)
        out = {"x": x[ix0:ix1], "y": y[iy0:iy1], "epsg": epsg,
               "spacing": float(abs(x[1] - x[0])), "channels": {}}
        for p in pols:
            name = p + p
            if name in g:
                a = g[name][iy0:iy1, ix0:ix1].astype(np.float32)
                a[~np.isfinite(a) | (a <= 0)] = np.nan
                out["channels"][p] = a
        if "numberOfLooks" in g:
            out["looks"] = float(np.nanmedian(g["numberOfLooks"][iy0:iy1, ix0:ix1]))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _find(f: h5py.File, ends: str, must: str = "unwrappedInterferogram") -> str | None:
    hits: list[str] = []
    f.visititems(lambda n, o: hits.append(n) if isinstance(o, h5py.Dataset) and n.endswith(ends) and must in n else None)
    hits.sort(key=lambda n: ("/HH/" not in n, len(n)))
    return hits[0] if hits else None


@_disk_cached("gunw3")
def read_gunw(url: str, lat: float, lon: float, half_km: float, with_los: bool = True) -> dict:
    with open_h5(url) as f:
        # known layout first: scanning the whole file costs ~9 s per product
        known = "science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase"
        ph_path = known if known in f else _find(f, "unwrappedPhase")
        if ph_path is None:
            raise ValueError("unwrappedPhase not found")
        grp = f[ph_path].parent
        xg = grp
        while "xCoordinates" not in xg and xg.name != "/":
            xg = xg.parent
        # the projection sits beside the coordinates (polar stereographic near the poles, UTM elsewhere)
        proj_grp = next((g_ for g_ in (grp, xg, f.get("/science/LSAR/GUNW/grids/frequencyA")) if g_ is not None and "projection" in g_), xg)
        epsg = _epsg(proj_grp, lat, lon)
        tr = Transformer.from_crs(4326, epsg, always_xy=True)
        cx, cy = tr.transform(lon, lat)
        half = half_km * 1000
        x = xg["xCoordinates"][:]
        y = xg["yCoordinates"][:]
        ix0, ix1 = _index_window(x, cx - half, cx + half)
        iy0, iy1 = _index_window(y, cy - half, cy + half)
        sl = (slice(iy0, iy1), slice(ix0, ix1))
        out = {"x": x[ix0:ix1], "y": y[iy0:iy1], "epsg": epsg,
               "phase": grp["unwrappedPhase"][sl].astype(np.float32),
               "phase_attrs": {k: str(v) for k, v in f[ph_path].attrs.items() if k in ("description", "units")}}
        for key, ds in (("coherence", "coherenceMagnitude"), ("components", "connectedComponents"),
                        ("iono", "ionospherePhaseScreen")):
            if ds in grp and grp[ds].shape == grp["unwrappedPhase"].shape:
                out[key] = grp[ds][sl].astype(np.float32)
        # line-of-sight unit vector (target -> sensor, east/north/up) at the window centre
        rg = "science/LSAR/GUNW/metadata/radarGrid"
        # viewing geometry is the same for every pair on a track: read it once (~9 s each time)
        if with_los and f"{rg}/losUnitVectorX" in f:
            gx, gy = f[f"{rg}/xCoordinates"][:], f[f"{rg}/yCoordinates"][:]
            ix = int(np.argmin(np.abs(gx - cx)))
            iy = int(np.argmin(np.abs(gy - cy)))
            e = float(np.nanmean(f[f"{rg}/losUnitVectorX"][:, iy, ix]))
            n = float(np.nanmean(f[f"{rg}/losUnitVectorY"][:, iy, ix]))
            inc = float(np.nanmean(f[f"{rg}/incidenceAngle"][:, iy, ix])) if f"{rg}/incidenceAngle" in f else None
            out["los"] = {"e": e, "n": n, "u": float(np.sqrt(max(0.0, 1 - e * e - n * n))), "incidence_deg": inc}
    return out


# ---------------------------------------------------------------- parallel reads
# h5py lets only one HDF5 call run at a time per process, so threads queue behind each
# other. Separate worker processes read truly in parallel (measured: 8 files in 36 s
# instead of ~190 s). The pool is created once and reused between analyses.
from concurrent.futures import ProcessPoolExecutor, as_completed

_pool = None
_pool_lock = threading.Lock()


def _gcov_job(args):
    return read_gcov(*args)


def _gunw_job(args):
    return read_gunw(*args)


def pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=int(os.getenv("READ_PROCESSES", "8")))
        return _pool


def read_parallel(kind: str, jobs: list[tuple], on_done=None) -> list:
    """Run read_gcov/read_gunw jobs in worker processes; on_done(index) after each."""
    fn = _gcov_job if kind == "gcov" else _gunw_job
    futs = {pool().submit(fn, j): i for i, j in enumerate(jobs)}
    out = [None] * len(jobs)
    for f in as_completed(futs):
        i = futs[f]
        out[i] = f.result()
        if on_done:
            on_done(i)
    return out
