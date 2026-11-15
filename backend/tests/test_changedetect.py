"""Validation of the change test on simulated speckle (gamma-distributed intensities).

These numbers are what the app quotes as its measured accuracy:
  - false-alarm rate on no-change pixels must stay near alpha
  - detection rate for a 3 dB step must be high, with the right change date
"""
import numpy as np

from app.changedetect import detect_changes, estimate_enl, sequential_omnibus

RNG = np.random.default_rng(42)


def speckle(mean, looks, shape):
    return RNG.gamma(shape=looks, scale=mean / looks, size=shape)


def make_stack(k=11, h=60, w=60, looks=9.0, step_at=None, step_db=3.0, region=None):
    hh = np.empty((k, h, w))
    hv = np.empty((k, h, w))
    for t in range(k):
        mhh = np.full((h, w), 0.10)
        mhv = np.full((h, w), 0.02)
        if step_at is not None and t >= step_at:
            f = 10 ** (step_db / 10)
            mhh[region] *= f
            mhv[region] /= f
        hh[t] = speckle(mhh, looks, (h, w))
        hv[t] = speckle(mhv, looks, (h, w))
    return {"HH": hh, "HV": hv}


def test_enl_estimate_close_to_truth():
    img = speckle(np.full((200, 200), 0.1), 9.0, (200, 200))
    assert 6.0 < estimate_enl(img) < 12.0


def test_false_alarm_rate_controlled():
    stack = make_stack()
    res = detect_changes(stack, enl=9.0, alpha=0.01)
    far = (res["change_count"] > 0).mean()
    print("false-alarm rate:", far)
    assert far < 0.03  # alpha = 1 %, allow sampling noise


def test_detects_3db_step_with_correct_date():
    """Operating point used by the pipeline: ~30 looks after 2x2 multilooking."""
    region = (slice(10, 50), slice(10, 50))
    stack = make_stack(step_at=6, region=region, looks=30.0)
    res = detect_changes(stack, enl=30.0, alpha=0.01)
    changed = res["change_count"][region] > 0
    det = changed.mean()
    right_date = (res["first_change"][region][changed] == 6).mean()
    outside = np.ones(res["change_count"].shape, bool)
    outside[region] = False
    far = (res["change_count"][outside] > 0).mean()
    print(f"detection {det:.3f}, correct date {right_date:.3f}, false alarms {far:.3f}")
    assert det > 0.95
    assert right_date > 0.9
    assert far < 0.03


def test_detects_transient_change_two_dates():
    """Flood-like: drop at 4, recovery at 8 -> both dates found."""
    k, h, w = 11, 40, 40
    hh = np.empty((k, h, w)); hv = np.empty((k, h, w))
    for t in range(k):
        f = 0.25 if 4 <= t < 8 else 1.0
        hh[t] = speckle(np.full((h, w), 0.1 * f), 30.0, (h, w))
        hv[t] = speckle(np.full((h, w), 0.02 * f), 30.0, (h, w))
    res = detect_changes({"HH": hh, "HV": hv}, enl=30.0, alpha=0.01)
    both = res["change_flags"][4] & res["change_flags"][8]
    print("both dates found:", both.mean())
    assert both.mean() > 0.9


def test_power_curve_report():
    """Prints detection rate vs step size (quoted in the app's 'How accurate is this?')."""
    region = (slice(0, 60), slice(0, 60))
    for db in (1.0, 1.5, 2.0, 3.0):
        stack = make_stack(step_at=6, step_db=db, region=region)
        res = detect_changes(stack, enl=9.0, alpha=0.01)
        print(f"step {db} dB: detection {(res['change_count'] > 0).mean():.3f}")
