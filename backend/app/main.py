"""
EarthEcho API. No user accounts, no login pages.

  GET  /api/health                 server status (is NISAR deep analysis enabled?)
  GET  /api/geocode?q=             place search
  GET  /api/reverse?lat=&lon=      place name
  GET  /api/coverage?lat=&lon=     every NISAR pass over a place (no login)
  GET  /api/site?lat=&lon=         terrain, land cover, rain (no login)
  GET  /api/osm?lat=&lon=          roads, rivers, buildings from OpenStreetMap (no login)
  POST /api/investigate            start a full NISAR change analysis (server token)
  GET  /api/jobs/{id}              progress + result
  GET  /api/validation             measured accuracy of the change detector
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import traceback
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, config, context, nisar

app = FastAPI(title="EarthEcho API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=2000)

JOBS: dict[str, dict] = {}
_lock = threading.Lock()


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:16]


def _cached(name: str, key: str, fn, ttl_s: float):
    path = config.CACHE_DIR / f"{name}_{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        return json.loads(path.read_text())
    out = fn()
    if not (isinstance(out, dict) and out.get("error")):  # never cache a failure
        path.write_text(json.dumps(out))
    return out


def _round(lat: float, lon: float):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "Latitude must be -90..90 and longitude -180..180.")
    return round(lat, 4), round(lon, 4)


@app.get("/api/health")
def health():
    return {"ok": True, "deep_analysis": config.has_earthdata(), "max_dates": config.MAX_DATES}


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=120)):
    try:
        return context.geocode(q.strip())
    except Exception as e:
        raise HTTPException(502, f"Place search is unavailable right now: {e}")


@app.get("/api/reverse")
def reverse(lat: float, lon: float):
    lat, lon = _round(lat, lon)
    return context.reverse(round(lat, 3), round(lon, 3))


@app.get("/api/coverage")
def coverage(lat: float, lon: float, half_km: float = Query(3.0, ge=1, le=6)):
    lat, lon = _round(lat, lon)
    try:
        return _cached("cov", _key(lat, lon, half_km), lambda: nisar.coverage(lat, lon, half_km), 3 * 3600)
    except Exception as e:
        raise HTTPException(502, f"ASF search did not answer: {e}")


@app.get("/api/site")
def site(lat: float, lon: float, half_km: float = Query(3.0, ge=1, le=6)):
    lat, lon = _round(lat, lon)
    return _cached("site", _key(lat, lon, half_km), lambda: analysis.site_profile(lat, lon, half_km), 12 * 3600)


@app.get("/api/osm")
def osm(lat: float, lon: float, half_km: float = Query(3.0, ge=1, le=6)):
    lat, lon = _round(lat, lon)
    key = _key(lat, lon, half_km)
    shipped = _shipped_osm(lat, lon) if half_km == 3.0 else None  # showcase places never depend on Overpass
    if shipped:
        return shipped
    return _cached("osm", key, lambda: analysis.osm_profile(lat, lon, half_km), 7 * 24 * 3600)


def _shipped_osm(lat: float, lon: float):
    """Roads and rivers saved with the app for the showcase places (within ~500 m of one)."""
    idx = config.ROOT / "demo" / "osm" / "index.json"
    if not idx.exists():
        return None
    for site in json.loads(idx.read_text()):
        if abs(site["lat"] - lat) <= 0.006 and abs(site["lon"] - lon) <= 0.006:
            return json.loads((idx.parent / site["file"]).read_text())
    return None


_LAYER_RE = __import__("re").compile(r"^[0-9a-f]{16}$")
_NAME_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}_(rgb|hh|hv)\.png$")


@app.get("/api/layers/{key}/{name}")
def layer(key: str, name: str):
    if not (_LAYER_RE.match(key) and _NAME_RE.match(name)):
        raise HTTPException(400, "Not a layer")
    for base in (config.CACHE_DIR / "layers", config.ROOT / "demo" / "layers"):
        f = base / key / name
        if f.exists():
            return FileResponse(f, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(404, "Layer not found; run the analysis again")


# ---- showcase places: curated list, results built and kept fresh by the server itself ----
DEMO = config.ROOT / "demo"
SHOWCASE_STATE: dict[str, str] = {}  # id -> "ready" | "building" | "refreshing" | "waiting" | "error: ..."


def _sites() -> list[dict]:
    p = DEMO / "showcase.json"
    sites = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    for s in sites:
        s["lat"], s["lon"] = snap(s["lat"]), snap(s["lon"])
        s["key"] = _key(s["lat"], s["lon"], 3.0, config.MAX_DATES)
    return sites


def _is_stale(site: dict, res: dict) -> bool:
    """A newer usable NISAR pass exists on the analysed track than the result's last date."""
    cov = nisar.coverage(site["lat"], site["lon"], 3.0)
    tr = next((t for t in cov["tracks"] if t["track"] == res["track"]["track"]), None)
    if not tr:
        return False
    usable = [a["date"] for a in tr["acquisitions"] if a.get("orbit") != "F"]
    return bool(usable) and max(usable) > res["dates"][-1]


def _build(site: dict, state: str):
    import shutil
    SHOWCASE_STATE[site["id"]] = state
    res = analysis.investigate(site["lat"], site["lon"], 3.0, layer_key=site["key"])
    (DEMO / f"inv_{site['key']}.json").write_text(json.dumps(res, allow_nan=False))
    src = config.CACHE_DIR / "layers" / site["key"]
    if src.exists():
        shutil.copytree(src, DEMO / "layers" / site["key"], dirs_exist_ok=True)
    with _lock:
        JOBS.pop(site["key"], None)  # next request reads the fresh file
    SHOWCASE_STATE[site["id"]] = "ready"


def _maintain_showcase():
    """Build missing showcase results; re-analyse any whose track has a newer NISAR pass. Every 6 h."""
    while True:
        for site in _sites():
            f = DEMO / f"inv_{site['key']}.json"
            try:
                if not f.exists():
                    if config.has_earthdata():
                        _build(site, "building")
                    else:
                        SHOWCASE_STATE[site["id"]] = "waiting"
                    continue
                SHOWCASE_STATE.setdefault(site["id"], "ready")
                if config.has_earthdata() and _is_stale(site, json.loads(f.read_text())):
                    _build(site, "refreshing")
            except Exception as e:
                traceback.print_exc()
                SHOWCASE_STATE[site["id"]] = f"error: {e}"
        time.sleep(6 * 3600)


def _remeasure_accuracy():
    """Re-run the detector validation at startup so the accuracy panel reflects the code that is running."""
    try:
        from . import validate_detector
        validate_detector.main()
        analysis.VALIDATION = json.loads((config.ROOT / "app" / "data" / "validation.json").read_text())
    except Exception:
        traceback.print_exc()


def _keep_awake(url: str):
    """Free hosts put an idle service to sleep after ~15 min; a light request through the public URL every
    10 min keeps the first visitor from waiting for a cold start."""
    import requests
    while True:
        time.sleep(600)
        try:
            requests.get(f"{url.rstrip('/')}/api/health", timeout=30)
        except Exception:
            pass


@app.on_event("startup")
def _start_background():
    threading.Thread(target=_maintain_showcase, daemon=True).start()
    threading.Thread(target=_remeasure_accuracy, daemon=True).start()
    public = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("KEEP_AWAKE_URL")
    if public and os.getenv("KEEP_AWAKE", "1") != "0":
        threading.Thread(target=_keep_awake, args=(public,), daemon=True).start()


@app.get("/api/showcase")
def showcase():
    out = []
    for s in _sites():
        f = DEMO / f"inv_{s['key']}.json"
        default = "ready" if f.exists() else ("queued" if config.has_earthdata() else "waiting")
        out.append(s | {"status": SHOWCASE_STATE.get(s["id"], default),
                        "updated": time.strftime("%Y-%m-%d", time.gmtime(f.stat().st_mtime)) if f.exists() else None})
    return out


@app.get("/api/validation")
def validation():
    return analysis.VALIDATION


class InvestigateIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    half_km: float = Field(3.0, ge=1, le=5)


def _run(job_id: str, lat: float, lon: float, half_km: float, key: str):
    def progress(p, msg, eta=None):
        with _lock:
            JOBS[job_id].update(progress=round(p, 3), message=msg,
                                eta_s=None if eta is None else round(eta), eta_at=time.time())
    try:
        res = analysis.investigate(lat, lon, half_km, progress, layer_key=key)
        (config.CACHE_DIR / f"inv_{key}.json").write_text(json.dumps(res, allow_nan=False))
        with _lock:
            JOBS[job_id].update(status="done", progress=1.0, message="Done", result=res)
    except Exception as e:
        traceback.print_exc()
        with _lock:
            JOBS[job_id].update(status="error", message=str(e))


def snap(v: float) -> float:
    """Analysis centres snap to a 0.01 degree (~1 km) grid so nearby clicks share one result."""
    return round(round(v / 0.01) * 0.01, 2)


@app.post("/api/investigate")
def investigate(body: InvestigateIn):
    lat, lon = _round(body.lat, body.lon)
    lat, lon = snap(lat), snap(lon)
    key = _key(lat, lon, body.half_km, config.MAX_DATES)
    cached = config.CACHE_DIR / f"inv_{key}.json"
    demo = config.ROOT / "demo" / f"inv_{key}.json"  # showcase results shipped in the repo
    if demo.exists():  # shipped showcase results are authoritative over any older local cache
        cached = demo
    job_id = key
    with _lock:
        if job_id in JOBS and JOBS[job_id]["status"] == "running":
            return {"job": job_id}
    if cached.exists() and (cached.parent.name == "demo" or time.time() - cached.stat().st_mtime < 24 * 3600):
        with _lock:
            JOBS[job_id] = {"status": "done", "progress": 1.0, "message": "Done (cached)",
                            "result": json.loads(cached.read_text())}
        return {"job": job_id}
    if not config.has_earthdata():
        raise HTTPException(503, "This public demo runs the full change analysis ahead of time for its nine showcase "
                                 "places. Everything else on the dashboard works live for this place.")
    with _lock:
        JOBS[job_id] = {"status": "running", "progress": 0.0, "message": "Queued", "started": time.time()}
    threading.Thread(target=_run, args=(job_id, lat, lon, body.half_km, key), daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    with _lock:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown analysis. Start it again from the dashboard.")
        return dict(j)


# ---- NASA GIBS tile proxy: retries out of the browser's sight, limits load, caches on disk ----
import re as _re
import threading as _th
from io import BytesIO as _BytesIO

import requests as _rq
from fastapi import Response

_GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
_TILE_RE = _re.compile(r"^[A-Za-z0-9_]+/default/[A-Za-z0-9_-]+/GoogleMapsCompatible_Level\d+/(\d+)/(\d+)/(\d+)\.(png|jpeg)$")
_gibs_sem = _th.BoundedSemaphore(8)
_gibs = _rq.Session()
_gibs.headers["User-Agent"] = config.USER_AGENT
_TILES = config.CACHE_DIR / "tiles"


def _gibs_get(path: str) -> tuple[int, bytes]:
    last = 0
    for i in range(4):
        try:
            with _gibs_sem:
                r = _gibs.get(_GIBS + path, timeout=20)
            if r.status_code == 200:
                return 200, r.content
            if r.status_code == 404:
                return 404, b""
            last = r.status_code
        except _rq.RequestException:
            last = 0
        time.sleep(0.5 * 2 ** i)
    return last or 502, b""


def _tile(path: str, depth: int = 0) -> tuple[int, bytes, str]:
    m = _TILE_RE.match(path)
    if not m:
        return 400, b"", ""
    mime = "image/png" if m.group(4) == "png" else "image/jpeg"
    cacheable = "/default/default/" not in path  # dated tiles never change
    f = _TILES / path
    if cacheable and f.exists():
        return 200, f.read_bytes(), mime
    code, data = _gibs_get(path)
    if code == 200:
        if cacheable:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        return 200, data, mime
    z, y, x = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if code == 404 or depth >= 2 or z == 0:
        return 404, b"", mime
    # parent fallback: upscale the matching quarter of the coarser tile
    parent = _re.sub(r"/\d+/\d+/\d+\.(png|jpeg)$", f"/{z - 1}/{y >> 1}/{x >> 1}.\\1", path)
    pc, pdata, _ = _tile(parent, depth + 1)
    if pc != 200:
        return 404, b"", mime
    from PIL import Image
    im = Image.open(_BytesIO(pdata))
    h = im.width // 2
    out = im.crop(((x & 1) * h, (y & 1) * h, (x & 1) * h + h, (y & 1) * h + h)).resize((256, 256), Image.NEAREST)
    buf = _BytesIO()
    out.save(buf, "PNG" if mime == "image/png" else "JPEG")
    return 200, buf.getvalue(), mime


@app.get("/api/gibs/{path:path}")
def gibs_tile(path: str):
    code, data, mime = _tile(path)
    if code != 200:
        return Response(status_code=code)
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


# ---- serve the built frontend (single deployment) ----
if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
