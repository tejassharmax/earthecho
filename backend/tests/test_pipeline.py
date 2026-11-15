"""End-to-end pipeline test with a planted change in simulated NISAR products.

Real terrain/land cover/rain are fetched for Joshimath; NISAR reads are simulated so
the test runs without an Earthdata token. Checks that the planted event is found on
the right date, with ground motion attached and hypotheses ranked.
"""
import numpy as np
import pytest
from pyproj import Transformer

from app import analysis, context, nisar

LAT, LON, HALF = 30.555, 79.56, 2.0
DATES = ["2026-06-21", "2026-07-03", "2026-07-15", "2026-07-27", "2026-09-01", "2026-09-13",
         "2026-09-25", "2026-10-07", "2026-10-19", "2026-10-31", "2026-11-12"]
STEP = 6  # change appears on 2026-09-25
RNG = np.random.default_rng(3)
EPSG = 32644
CX, CY = Transformer.from_crs(4326, EPSG, always_xy=True).transform(LON, LAT)
PATCH = (CX + 400, CY - 300, 250)  # x, y, radius (m)


def fake_gcov(url, lat, lon, half_km, pols):
    i = DATES.index(url)
    x = np.arange(CX - 2100, CX + 2100, 10.0) + 5
    y = np.arange(CY + 2100, CY - 2100, -10.0) - 5
    X, Y = np.meshgrid(x, y)
    inside = np.hypot(X - PATCH[0], Y - PATCH[1]) < PATCH[2]
    hh = np.full(X.shape, 0.08)
    hv = np.full(X.shape, 0.02)
    if i >= STEP:
        hh[inside] *= 10 ** (2 / 10)   # roughness up
        hv[inside] /= 10 ** (4 / 10)   # canopy lost
    looks = 8
    ch = {"HH": RNG.gamma(looks, hh / looks).astype(np.float32),
          "HV": RNG.gamma(looks, hv / looks).astype(np.float32)}
    return {"x": x, "y": y, "epsg": EPSG, "spacing": 10.0, "channels": ch, "looks": looks, "seconds": 0.0}


def fake_gunw(url, lat, lon, half_km, with_los=True):
    x = np.arange(CX - 2100, CX + 2100, 80.0) + 40
    y = np.arange(CY + 2100, CY - 2100, -80.0) - 40
    X, Y = np.meshgrid(x, y)
    inside = np.hypot(X - PATCH[0], Y - PATCH[1]) < PATCH[2] + 100
    mm = np.where(inside, -8.0, 0.0) + RNG.normal(0, 1.0, X.shape)
    phase = mm / 1000 * 4 * np.pi / nisar.LAMBDA_L
    return {"x": x, "y": y, "epsg": EPSG, "phase": phase.astype(np.float32),
            "coherence": np.full(X.shape, 0.6, np.float32), "components": np.ones(X.shape, np.float32)}


@pytest.fixture
def patched(monkeypatch):
    track = {"track": "106_D_073", "direction": "descending", "n_dates": len(DATES),
             "pols_always": ["HH", "HV"], "next_expected_pass": "2026-11-24", "min_coverage": 1.0,
             "acquisitions": [{"date": d} for d in DATES]}
    monkeypatch.setattr(nisar, "coverage", lambda *a, **k: {"tracks": [track]})
    gc = [{"track": "106_D_073", "date": d, "url": d, "maturity": "PROVISIONAL", "mode": "DHDH"} for d in DATES]
    gu = [{"track": "106_D_073", "name": f"NISAR_L2_PR_GUNW_x_{a.replace('-', '')}T000000_{a.replace('-', '')}T000001_"
                                         f"{b.replace('-', '')}T000000_{b.replace('-', '')}T000001_P05023", "url": a}
          for a, b in zip(DATES[:-1], DATES[1:])]
    monkeypatch.setattr(nisar, "search", lambda level, aoi, **k: gc if level == "GCOV" else gu)
    monkeypatch.setattr(nisar, "read_gcov", fake_gcov)
    monkeypatch.setattr(nisar, "read_gunw", fake_gunw)

    def in_process(kind, jobs, on_done=None):  # worker processes can't see monkeypatched readers
        out = []
        for i, j in enumerate(jobs):
            out.append(fake_gcov(*j) if kind == "gcov" else fake_gunw(*j))
            if on_done:
                on_done(i)
        return out
    monkeypatch.setattr(nisar, "read_parallel", in_process)
    monkeypatch.setattr(context, "osm", lambda *a: {"roads": [], "rivers": [], "water": [], "buildings": 0})


def test_planted_event_found(patched):
    res = analysis.investigate(LAT, LON, HALF)
    assert res["events"], "no events found"
    ev = max(res["events"], key=lambda e: e["area_ha"])
    print({k: ev[k] for k in ("id", "date_first", "area_ha", "dHH_db", "dHV_db", "slope_deg", "landcover",
                              "los_rate_mm_per_12d", "los_consistent_pairs")})
    print("top hypotheses:", [(h["name"], h["score"]) for h in ev["hypotheses"][:3]])
    print("priority:", ev["priority"]["level"], ev["priority"]["score"], "changed fraction:", res["changed_fraction"])
    true_area = np.pi * PATCH[2] ** 2 / 1e4
    assert ev["date_first"] == DATES[STEP]
    assert 0.6 * true_area < ev["area_ha"] < 1.5 * true_area
    assert ev["dHV_db"] < -3 and ev["dHH_db"] > 1
    assert res["changed_fraction"] < 0.05
    assert ev["los_series"] is not None
    assert len(res["events"]) <= 3, "too many false events"


def test_result_is_strict_json(patched):
    import json
    res = analysis.investigate(LAT, LON, HALF)
    json.dumps(res, allow_nan=False)  # raises on NaN/ndarray: browsers reject both
