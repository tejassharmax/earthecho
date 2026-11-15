"""
Global context layers, no login: Copernicus DEM GLO-30, ESA WorldCover 10 m,
NASA POWER daily rainfall, OpenStreetMap roads/rivers/buildings, place search.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from . import config

GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                AWS_NO_SIGN_REQUEST="YES", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="1")

WORLDCOVER = {10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "Built-up",
              60: "Bare / sparse vegetation", 70: "Snow and ice", 80: "Permanent water",
              90: "Herbaceous wetland", 95: "Mangroves", 100: "Moss and lichen"}


def utm_epsg(lat: float, lon: float) -> int:
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def make_grid(lat: float, lon: float, half_km: float, spacing: float, epsg: int | None = None):
    epsg = epsg or utm_epsg(lat, lon)
    cx, cy = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    half = half_km * 1000
    x0 = math.floor((cx - half) / spacing) * spacing
    y1 = math.ceil((cy + half) / spacing) * spacing
    n = int(round(2 * half / spacing))
    return {"epsg": epsg, "transform": from_origin(x0, y1, spacing, spacing), "shape": (n, n),
            "spacing": spacing, "x0": x0, "y1": y1}


def _dem_tiles(bounds):
    w, s, e, n = bounds
    for la in range(math.floor(s), math.floor(n) + 1):
        for lo in range(math.floor(w), math.floor(e) + 1):
            ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
            ew = f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}"
            name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
            yield f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"


def _wc_tiles(bounds):
    w, s, e, n = bounds
    for la in range(math.floor(s / 3) * 3, math.floor(n / 3) * 3 + 1, 3):
        for lo in range(math.floor(w / 3) * 3, math.floor(e / 3) * 3 + 1, 3):
            ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
            ew = f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}"
            yield f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif"


def _warp(urls, grid, resampling, dtype, nodata):
    out = np.full(grid["shape"], nodata, dtype=dtype)
    with rasterio.Env(**GDAL_ENV):
        for url in urls:
            try:
                with rasterio.open(url) as src:
                    tmp = np.full(grid["shape"], nodata, dtype=dtype)
                    reproject(rasterio.band(src, 1), tmp, dst_transform=grid["transform"],
                              dst_crs=f"EPSG:{grid['epsg']}", dst_nodata=nodata, resampling=resampling)
                    fill = (out == nodata) if not np.isnan(nodata) else np.isnan(out)
                    out[fill] = tmp[fill]
            except rasterio.errors.RasterioIOError:
                continue  # ocean tile or missing tile
    return out


def grid_bounds_lonlat(grid):
    h, w = grid["shape"]
    s = grid["spacing"]
    xs = [grid["x0"], grid["x0"] + w * s]
    ys = [grid["y1"] - h * s, grid["y1"]]
    tr = Transformer.from_crs(grid["epsg"], 4326, always_xy=True)
    corners = [tr.transform(x, y) for x, y in ((xs[0], ys[1]), (xs[1], ys[1]), (xs[1], ys[0]), (xs[0], ys[0]))]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return (min(lons) - 0.01, min(lats) - 0.01, max(lons) + 0.01, max(lats) + 0.01), corners


def terrain(grid) -> dict:
    bounds, _ = grid_bounds_lonlat(grid)
    dem = _warp(_dem_tiles(bounds), grid, Resampling.bilinear, np.float32, np.nan)
    gy, gx = np.gradient(dem, grid["spacing"])
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    aspect = (np.degrees(np.arctan2(-gx, gy)) + 360) % 360  # downslope direction, 0 = north
    return {"elevation": dem, "slope": slope, "aspect": aspect}


def landcover(grid) -> np.ndarray:
    bounds, _ = grid_bounds_lonlat(grid)
    return _warp(_wc_tiles(bounds), grid, Resampling.mode, np.uint8, 0)


@lru_cache(maxsize=256)
def rainfall(lat: float, lon: float, start: str = "20260601") -> dict:
    end = date.today().strftime("%Y%m%d")
    r = requests.get("https://power.larc.nasa.gov/api/temporal/daily/point",
                     params=dict(parameters="PRECTOTCORR", community="AG", longitude=round(lon, 3),
                                 latitude=round(lat, 3), start=start, end=end, format="JSON"),
                     headers={"User-Agent": config.USER_AGENT}, timeout=60)
    r.raise_for_status()
    d = r.json()["properties"]["parameter"]["PRECTOTCORR"]
    days = sorted(d)
    return {"dates": [f"{k[:4]}-{k[4:6]}-{k[6:]}" for k in days],
            "mm": [None if d[k] is None or d[k] < 0 else round(float(d[k]), 2) for k in days],
            "source": "NASA POWER (MERRA-2 / IMERG-corrected precipitation, PRECTOTCORR)"}


def rain_before(rain: dict, day: str, ndays: int = 30) -> float | None:
    if not rain or not rain.get("dates"):
        return None
    d1 = date.fromisoformat(day)
    d0 = d1 - timedelta(days=ndays)
    vals = [v for k, v in zip(rain["dates"], rain["mm"]) if v is not None and d0 < date.fromisoformat(k) <= d1]
    return round(sum(vals), 1) if vals else None


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
_osm_ok: dict = {}  # successful answers only; a failure is retried on the next request


def osm(s: float, w: float, n: float, e: float) -> dict:
    key = (s, w, n, e)
    if key in _osm_ok:
        return _osm_ok[key]
    for attempt in range(2):  # public Overpass servers are often busy for a few seconds at a time
        out = _osm_fetch(s, w, n, e)
        if not out.get("error"):
            if len(_osm_ok) > 256:
                _osm_ok.clear()
            _osm_ok[key] = out
            return out
        time.sleep(2 + 3 * attempt)
    return out


def _osm_fetch(s: float, w: float, n: float, e: float) -> dict:
    bb = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    q = (f"[out:json][timeout:40];("
         f'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|track)$"]({bb});'
         f'way["waterway"~"^(river|stream|canal)$"]({bb});'
         f'way["natural"="water"]({bb});'
         f");out geom;way[\"building\"]({bb});out count;")
    last = None
    for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": config.USER_AGENT}, timeout=(8, 35))
            r.raise_for_status()
            els = r.json().get("elements", [])
            roads, rivers, water = [], [], []
            buildings = 0
            for el in els:
                if el.get("type") == "count":
                    buildings = int(el.get("tags", {}).get("ways", 0))
                    continue
                geom = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
                tags = el.get("tags", {})
                if "highway" in tags:
                    roads.append({"kind": tags["highway"], "name": tags.get("name"), "coords": geom})
                elif "waterway" in tags:
                    rivers.append({"kind": tags["waterway"], "name": tags.get("name"), "coords": geom})
                else:
                    water.append({"kind": "water", "name": tags.get("name"), "coords": geom})
            return {"roads": roads, "rivers": rivers, "water": water, "buildings": buildings,
                    "source": "OpenStreetMap contributors (ODbL)"}
        except Exception as ex:  # try the mirror
            last = ex
    return {"roads": [], "rivers": [], "water": [], "buildings": None, "error": str(last)}


def nearest(lines: list[dict], lon: float, lat: float, epsg: int) -> dict | None:
    """Distance (m) from a point to the nearest vertex-densified line, plus its name."""
    if not lines:
        return None
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    px, py = tr.transform(lon, lat)
    best = None
    for ln in lines:
        if len(ln["coords"]) < 2:
            continue
        xs, ys = tr.transform([c[0] for c in ln["coords"]], [c[1] for c in ln["coords"]])
        xs, ys = np.asarray(xs), np.asarray(ys)
        ax, ay, bx, by = xs[:-1], ys[:-1], xs[1:], ys[1:]
        dx, dy = bx - ax, by - ay
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.maximum(dx * dx + dy * dy, 1e-9), 0, 1)
        qx, qy = ax + t * dx, ay + t * dy
        d = np.hypot(px - qx, py - qy)
        i = int(np.argmin(d))
        if best is None or d[i] < best["distance_m"]:
            lo, la = Transformer.from_crs(epsg, 4326, always_xy=True).transform(qx[i], qy[i])
            best = {"distance_m": round(float(d[i]), 1), "name": ln.get("name"), "kind": ln["kind"],
                    "point": [round(lo, 6), round(la, 6)]}
    return best


_nom_lock = threading.Lock()
_nom_last = [0.0]


def _nominatim(path: str, params: dict):
    with _nom_lock:  # usage policy: max 1 request per second
        wait = 1.05 - (time.time() - _nom_last[0])
        if wait > 0:
            time.sleep(wait)
        r = requests.get(f"https://nominatim.openstreetmap.org/{path}", params=params | {"format": "jsonv2"},
                         headers={"User-Agent": config.USER_AGENT}, timeout=20)
        _nom_last[0] = time.time()
    r.raise_for_status()
    return r.json()


@lru_cache(maxsize=512)
def geocode(q: str) -> list[dict]:
    res = _nominatim("search", {"q": q, "limit": 6})
    return [{"name": x.get("display_name"), "lat": float(x["lat"]), "lon": float(x["lon"]),
             "type": x.get("type")} for x in res]


def reverse(lat: float, lon: float) -> dict:
    try:
        return _reverse(lat, lon)
    except Exception:  # not cached, so the name appears once Nominatim answers again
        return {"name": None}


@lru_cache(maxsize=1024)
def _reverse(lat: float, lon: float) -> dict:
    x = _nominatim("reverse", {"lat": lat, "lon": lon, "zoom": 12})
    a = x.get("address", {})
    short = a.get("city") or a.get("town") or a.get("village") or a.get("county") or a.get("state_district") or a.get("state")
    return {"name": short or x.get("display_name"), "full": x.get("display_name"),
            "country": a.get("country"), "country_code": a.get("country_code")}
