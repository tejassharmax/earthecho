"""Two viewing geometries -> vertical and east-west motion (sign-independent split)."""
import numpy as np

from app.analysis import _decompose


def geom(e, n):
    return {"e": e, "n": n, "u": float(np.sqrt(1 - e * e - n * n))}


def test_recovers_vertical_and_eastwest():
    U_true, E_true = -6.0, 2.5            # mm per 12 days
    asc, desc = geom(-0.58, -0.12), geom(0.58, -0.12)   # NISAR-like look directions (incidence ~37 deg)
    x = np.arange(0, 800, 80.0)
    y = np.arange(800, 0, -80.0)
    g = {"epsg": 32644, "x": x, "y": y, "dx": 80.0}
    shape = (len(y), len(x))
    v1 = np.full(shape, E_true * asc["e"] + U_true * asc["u"])
    v2 = np.full(shape, E_true * desc["e"] + U_true * desc["u"])
    m1 = {"los": asc, "_grid": g, "_vel": v1, "track": "A", "n_pairs": 8, "corners": []}
    m2 = {"los": desc, "_grid": g, "_vel": v2, "track": "D", "n_pairs": 8, "corners": []}
    out = _decompose(m1, m2)
    assert np.allclose(out["_U"], U_true, atol=1e-6)
    assert np.allclose(out["_E"], E_true, atol=1e-6)
    assert out["sign_confirmed"] is False
    # flipping the unknown sign convention flips both, so the vertical share is unchanged
    m1["_vel"], m2["_vel"] = -v1, -v2
    flipped = _decompose(m1, m2)
    assert flipped["median_vertical_share"] == out["median_vertical_share"]
