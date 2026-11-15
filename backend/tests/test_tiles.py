"""GIBS tile proxy: real tiles, no-data days, path validation, parent fallback."""
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
T = "NISAR_L2_Geocoded_Polarimetric_Covariance/default/2026-11-09/GoogleMapsCompatible_Level13"


def test_real_tile_and_cache():
    r = client.get(f"/api/gibs/{T}/12/1690/2937.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert "max-age" in r.headers["cache-control"]


def test_no_data_day_is_404():
    r = client.get("/api/gibs/NISAR_L2_Geocoded_Polarimetric_Covariance/default/2030-01-01/GoogleMapsCompatible_Level13/12/1690/2937.png")
    assert r.status_code == 404


def test_rejects_non_tile_paths():
    assert main._tile("../../etc/passwd")[0] == 400                 # no path traversal
    assert main._tile(f"{T}/../../../../etc/12/1/1.png")[0] == 400  # no dots allowed anywhere
    assert client.get("/api/gibs/anything/else.png").status_code == 400


def test_parent_fallback_when_gibs_keeps_failing(monkeypatch):
    real = main._gibs_get
    monkeypatch.setattr(main, "_gibs_get", lambda path: (500, b"") if "/13/" in path else real(path))
    r = client.get(f"/api/gibs/{T}/13/3381/5874.png?nocache")
    path = main._TILES / f"{T}/13/3381/5874.png"
    if path.exists():
        path.unlink()  # make sure the cached copy does not hide the fallback
        r = client.get(f"/api/gibs/{T}/13/3381/5874.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
