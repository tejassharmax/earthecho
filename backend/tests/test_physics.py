"""Physics added after review: radar geometry, seasonal detrending, correlated channels, downhill paths."""
import numpy as np

from app import physics
from app.changedetect import detect_changes
from app.validate_detector import speckle_pair

RNG = np.random.default_rng(11)


def plane(slope_deg, dip_toward_east, n=50, spacing=20.0):
    """Elevation plane descending towards east (True) or west (False)."""
    x = np.arange(n) * spacing
    z = np.tan(np.radians(slope_deg)) * (x if not dip_toward_east else -x)
    return np.tile(z, (n, 1))


def test_layover_and_shadow_from_slope_and_look_direction():
    los = physics.los_vector("descending")          # descending looks west: sensor sits to the east
    assert los[0] > 0                                # target->sensor points east
    facing = physics.radar_geometry(plane(45, True), 20.0, los)   # descends towards the sensor: faces it
    away = physics.radar_geometry(plane(60, False), 20.0, los)    # descends away from the sensor
    flat = physics.radar_geometry(plane(0, True), 20.0, los)
    assert facing["layover"].mean() > 0.9 and not facing["shadow"].any()
    assert away["shadow"].mean() > 0.9 and not away["layover"].any()
    assert not flat["layover"].any() and not flat["shadow"].any()


def test_seasonal_shift_is_removed_but_local_change_survives():
    k, h, w = 11, 60, 60
    lc = np.full((h, w), 40, np.uint8)               # all cropland
    hh = np.empty((k, h, w)); hv = np.empty((k, h, w))
    for t in range(k):
        season = 10 ** ((-2.5 if t >= 5 else 0.0) / 10)          # whole class drops 2.5 dB (drying)
        hh[t], hv[t] = speckle_pair(0.1 * season, 0.02 * season, 30, 0.3, (h, w))
        if t >= 7:                                                # plus a local 4 dB loss of HV
            hv[t, 20:35, 20:35] /= 10 ** 0.4
    raw = detect_changes({"HH": hh, "HV": hv}, enl=30, alpha=0.01)
    detr, curves = physics.seasonal_detrend({"HH": hh, "HV": hv}, lc)
    fixed = detect_changes(detr, enl=30, alpha=0.01)
    outside = np.ones((h, w), bool); outside[20:35, 20:35] = False
    assert (raw["change_count"][outside] > 0).mean() > 0.5         # without detrending: season floods detector
    assert (fixed["change_count"][outside] > 0).mean() < 0.03      # with it: only false-alarm level
    assert (fixed["change_count"][20:35, 20:35] > 0).mean() > 0.9  # the real change is still found
    assert 40 in curves and len(curves[40]["HH"]) == k


def test_correlated_channels_stay_calibrated():
    k, h, w = 11, 80, 80
    hh = np.empty((k, h, w)); hv = np.empty((k, h, w))
    for t in range(k):
        hh[t], hv[t] = speckle_pair(0.1, 0.02, 30, 0.9, (h, w))
    st = {"HH": hh, "HV": hv}
    r = physics.channel_corr(st)
    far = (detect_changes(st, enl=30, alpha=0.01, chan_corr=r)["change_count"] > 0).mean()
    assert r > 0.3
    assert far < 0.012


def test_downhill_path_reaches_river_below_not_road_above():
    elev = plane(20, True, n=60)                     # descends east
    path = physics.downslope_path(elev, (30, 5))
    cols = [c for _, c in path]
    assert cols[-1] == 59 and all(b >= a for a, b in zip(cols, cols[1:]))   # goes downhill (east)
    river = np.zeros(elev.shape, bool); river[:, 40] = True              # river downhill
    road = np.zeros(elev.shape, bool); road[:, 2] = True                 # road uphill
    assert any(river[r, c] for r, c in path)
    assert not any(road[r, c] for r, c in path[1:])
