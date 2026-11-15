"""
Measures the change detector on simulated NISAR-like speckle and writes
app/data/validation.json, shown in the dashboard under "How accurate is this?".

Speckle is simulated physically: each look is a complex Gaussian (HH, HV) pair with a chosen
HH-HV coherence, so the two channels are correlated as in forests and cities. That checks the
false-alarm rate when channels are NOT independent, with and without EarthEcho's measured
correlation correction.
Run:  python -m app.validate_detector
"""
import json
import os

import numpy as np

from app.changedetect import detect_changes
from app.physics import channel_corr

RNG = np.random.default_rng(7)
K, H, W, STEP_AT = 11, 80, 80, 6


def speckle_pair(mean_hh, mean_hv, looks, coherence, shape):
    """Multilook intensities from correlated complex Gaussian looks."""
    hh = np.zeros(shape); hv = np.zeros(shape)
    for _ in range(int(looks)):
        z1 = (RNG.normal(size=shape) + 1j * RNG.normal(size=shape)) / np.sqrt(2)
        w = (RNG.normal(size=shape) + 1j * RNG.normal(size=shape)) / np.sqrt(2)
        z2 = coherence * z1 + np.sqrt(1 - coherence ** 2) * w
        hh += np.abs(z1) ** 2; hv += np.abs(z2) ** 2
    return mean_hh * hh / looks, mean_hv * hv / looks


def make(looks, step_db, coherence):
    hh = np.empty((K, H, W)); hv = np.empty((K, H, W))
    for t in range(K):
        f = 10 ** (step_db / 10) if (step_db and t >= STEP_AT) else 1.0
        hh[t], hv[t] = speckle_pair(0.10 * f, 0.02 / f, looks, coherence, (H, W))
    return {"HH": hh, "HV": hv}


def run(looks, step_db, coherence, corrected=True, alpha=0.01):
    st = make(looks, step_db, coherence)
    r = channel_corr(st) if corrected else 0.0
    res = detect_changes(st, enl=looks, alpha=alpha, chan_corr=r)
    changed = res["change_count"] > 0
    out = {"rate": float(changed.mean()), "measured_r": r}
    if step_db:
        out["correct_date"] = float((res["first_change"][changed] == STEP_AT).mean()) if changed.any() else 0.0
    return out


def main():
    res = {"method": "Gamma likelihood-ratio change-point test with Conradsen (2016) omnibus gate; "
                     "HH and HV combined with a Satterthwaite correction for their measured correlation",
           "alpha": 0.01, "images": K, "pixels_per_case": H * W,
           "note": "Simulated speckle from correlated complex-Gaussian looks (HH-HV coherence 0 to 0.9), "
                   "not real NISAR labels.",
           "cases": [], "correlation_check": []}
    for looks in (9, 30):
        res["cases"].append({"looks": looks, "step_db": 0, "false_alarm_rate": run(looks, 0, 0.5)["rate"]})
        for db in (1, 2, 3, 6):
            o = run(looks, db, 0.5)
            res["cases"].append({"looks": looks, "step_db": db, "detection_rate": o["rate"], "correct_date_rate": o["correct_date"]})
    for coh in (0.0, 0.5, 0.7, 0.9):
        naive = run(30, 0, coh, corrected=False)
        fixed = run(30, 0, coh, corrected=True)
        res["correlation_check"].append({"hh_hv_coherence": coh, "false_alarms_naive": naive["rate"],
                                         "false_alarms_corrected": fixed["rate"], "measured_r": fixed["measured_r"]})
    path = os.path.join(os.path.dirname(__file__), "data", "validation.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2)
    for c in res["cases"] + res["correlation_check"]:
        print(c)


if __name__ == "__main__":
    main()
