"""
Multitemporal SAR change detection with a controlled false-alarm rate.

Implements the sequential omnibus likelihood-ratio test of
  Conradsen, Nielsen & Skriver (2016), "Determining the points of change in time
  series of polarimetric SAR data", IEEE TGRS 54(5), 3007-3024,
for the diagonal (intensity-only) case: each polarization channel (HH, HV, ...) is a
gamma-distributed multilook intensity with equivalent number of looks (ENL) n.

Why this instead of a trained CV model: there is no labelled NISAR change dataset, so
a learned model's accuracy cannot be measured. This test's false-alarm rate is set by
the significance level alpha and is verified on simulated speckle in tests/.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter
from scipy.stats import chi2

EPS = 1e-12


def estimate_enl(intensity: np.ndarray, win: int = 7) -> float:
    """Equivalent number of looks = mode of local mean^2/var over homogeneous windows."""
    a = np.where(np.isfinite(intensity) & (intensity > 0), intensity, np.nan)
    valid = np.isfinite(a)
    if valid.sum() < win * win * 4:
        return 4.0
    filled = np.where(valid, a, 0.0)
    cnt = uniform_filter(valid.astype(float), win)
    m1 = uniform_filter(filled, win) / np.maximum(cnt, EPS)
    m2 = uniform_filter(filled ** 2, win) / np.maximum(cnt, EPS)
    var = m2 - m1 ** 2
    ok = (cnt > 0.99) & (var > 0) & (m1 > 0)
    enl = (m1[ok] ** 2) / var[ok]
    enl = enl[(enl > 0.5) & (enl < 500)]
    if enl.size < 50:
        return 4.0
    hist, edges = np.histogram(np.log(enl), bins=60)
    i = int(np.argmax(hist))
    return float(np.exp(0.5 * (edges[i] + edges[i + 1])))


def _pvalue(z: np.ndarray, f: np.ndarray, omega2: np.ndarray) -> np.ndarray:
    """Box (1949) approximation used in Conradsen et al. (2016)."""
    p0 = chi2.cdf(z, f)
    p4 = chi2.cdf(z, f + 4)
    return np.clip(1.0 - (p0 + omega2 * (p4 - p0)), 0.0, 1.0)


def _omnibus_z(logsum: np.ndarray, total: np.ndarray, m: np.ndarray, n: float):
    """-2*rho*lnQ for 'all m images equal' (p=1), plus rho and omega2."""
    lnq = n * (m * np.log(m) + logsum - m * np.log(np.maximum(total, EPS)))
    rho = 1.0 - (m + 1.0) / (6.0 * m * n)
    omega2 = -(m - 1.0) / 4.0 * (1.0 - 1.0 / rho) ** 2
    return -2.0 * rho * lnq, rho, omega2


def _rj_z(prev_sum, cur, all_sum, j, n):
    """-2*rho*lnR_j: is image j equal to the j-1 images before it (p=1)?"""
    jm1 = j - 1.0
    lnr = n * (j * np.log(j) - jm1 * np.log(np.maximum(jm1, 1.0)) * (jm1 > 0)
               + jm1 * np.log(np.maximum(prev_sum, EPS)) + np.log(np.maximum(cur, EPS))
               - j * np.log(np.maximum(all_sum, EPS)))
    rho = 1.0 - (1.0 / (6.0 * n)) * (1.0 + 1.0 / (j * jm1))
    omega2 = -0.25 * (1.0 - 1.0 / rho) ** 2
    return -2.0 * rho * lnr, rho, omega2


def _split_z(m1, m2, c1, c2, n):
    """Bartlett-corrected -2 ln(LR) for 'mean of images before j == mean after j' (gamma, p=1)."""
    a, b = n * c1, n * c2
    mbar = (c1 * m1 + c2 * m2) / (c1 + c2)
    z = 2.0 * (a * np.log(np.maximum(mbar, EPS) / np.maximum(m1, EPS))
               + b * np.log(np.maximum(mbar, EPS) / np.maximum(m2, EPS)))
    rho = 1.0 - (1.0 / 6.0) * (1.0 / a + 1.0 / b - 1.0 / (a + b))
    return rho * z


def detect_changes(stack: dict[str, np.ndarray], enl: float, alpha: float = 0.01,
                   max_rounds: int = 4, chan_corr: float = 0.0) -> dict[str, np.ndarray]:
    """
    Likelihood-ratio binary segmentation with a union gate.
      1. Date the change: the split j maximizing the two-sample gamma likelihood ratio
         (uses every image on both sides, so a single noisy image cannot hide a change),
         Bonferroni-adjusted for the number of candidate splits.
      2. Gate (false-alarm control, total <= alpha): accept if the split test passes at
         alpha/2, or if the Conradsen omnibus test passes at alpha/2 and the split at alpha.
      3. Recurse on both sub-segments (captures e.g. flood onset AND recession).
    stack: {"HH": (k, H, W), "HV": ...} linear intensities; NaN = no data.
    """
    chans = list(stack)
    k, h, w = stack[chans[0]].shape
    n = float(enl)
    npix = h * w
    data = {c: stack[c].reshape(k, npix).astype(np.float64) for c in chans}
    valid = np.ones(npix, bool)
    for c in chans:
        valid &= np.all(np.isfinite(data[c]) & (data[c] > 0), axis=0)
    csum = {c: np.vstack([np.zeros(npix), np.cumsum(np.where(valid, data[c], 1.0), axis=0)]) for c in chans}
    lsum = {c: np.vstack([np.zeros(npix), np.cumsum(np.log(np.where(valid, data[c], 1.0)), axis=0)]) for c in chans}
    nch = len(chans)
    flags = np.zeros((k, npix), bool)
    min_p = np.ones(npix)

    pix = np.where(valid)[0]
    seg_s = np.zeros(pix.size, int)
    seg_e = np.full(pix.size, k)
    for _ in range(max_rounds):
        keep = (seg_e - seg_s) >= 2
        pix, seg_s, seg_e = pix[keep], seg_s[keep], seg_e[keep]
        if pix.size == 0:
            break
        m = (seg_e - seg_s).astype(float)
        zq = np.zeros(pix.size)
        omq = np.zeros(pix.size)
        for c in chans:
            tot = csum[c][seg_e, pix] - csum[c][seg_s, pix]
            lg = lsum[c][seg_e, pix] - lsum[c][seg_s, pix]
            z, _, om = _omnibus_z(lg, tot, m, n)
            zq += z
            omq += om
        # Satterthwaite: channels whose statistics correlate by r -> sum/(1+r) ~ chi2 with f/(1+r) dof
        cq = 1.0 + chan_corr * (nch - 1)
        pq = _pvalue(zq / cq, nch * (m - 1) / cq, omq / nch)
        best_z = np.full(pix.size, -np.inf)
        best_j = np.full(pix.size, -1)
        for j in range(1, k):
            ok = (j > seg_s) & (j < seg_e)
            if not ok.any():
                continue
            zj = np.full(pix.size, -np.inf)
            c1 = (j - seg_s[ok]).astype(float)
            c2 = (seg_e[ok] - j).astype(float)
            acc = np.zeros(ok.sum())
            for c in chans:
                p_ = pix[ok]
                m1 = (csum[c][j, p_] - csum[c][seg_s[ok], p_]) / c1
                m2 = (csum[c][seg_e[ok], p_] - csum[c][j, p_]) / c2
                acc += _split_z(m1, m2, c1, c2, n)
            zj[ok] = acc
            better = zj > best_z
            best_z[better] = zj[better]
            best_j[better] = j
        p_split = np.clip(chi2.sf(best_z / cq, nch / cq) * (m - 1), 0, 1)  # Bonferroni over splits
        # Union gate, each half of alpha, so the false-alarm rate stays <= alpha:
        #   omnibus (any kind of change)  OR  max-split (step change, much more power)
        hit = (p_split < alpha / 2) | ((pq < alpha / 2) & (p_split < alpha))
        flags[best_j[hit], pix[hit]] = True
        min_p[pix[hit]] = np.minimum(min_p[pix[hit]], p_split[hit])
        hp, hs, he, hj = pix[hit], seg_s[hit], seg_e[hit], best_j[hit]
        pix = np.concatenate([hp, hp])
        seg_s = np.concatenate([hs, hj])
        seg_e = np.concatenate([hj, he])

    count = flags.sum(axis=0).astype(int)
    first = np.where(count > 0, np.argmax(flags, axis=0), -1)
    last = np.where(count > 0, k - 1 - np.argmax(flags[::-1], axis=0), -1)
    count = np.where(valid, count, -1)
    return {
        "change_count": count.reshape(h, w),
        "first_change": first.reshape(h, w),
        "last_change": last.reshape(h, w),
        "change_flags": flags.reshape(k, h, w),
        "min_p": min_p.reshape(h, w),
        "enl": n,
        "alpha": alpha,
        "chan_corr": chan_corr,
    }


def sequential_omnibus(stack: dict[str, np.ndarray], enl: float, alpha: float = 0.01,
                       max_changes: int | None = None) -> dict[str, np.ndarray]:
    """
    The paper's original sequential R_j procedure (kept for comparison in tests).
    Lower power when ENL is small because each R_j tests a single new image.
    """
    chans = list(stack)
    k, h, w = stack[chans[0]].shape
    n = float(enl)
    npix = h * w
    data = {c: stack[c].reshape(k, npix).astype(np.float64) for c in chans}
    valid = np.ones(npix, bool)
    for c in chans:
        valid &= np.all(np.isfinite(data[c]) & (data[c] > 0), axis=0)
    # prefix sums (index 0 = empty) for O(1) segment sums
    csum = {c: np.vstack([np.zeros(npix), np.cumsum(np.where(valid, data[c], 1.0), axis=0)]) for c in chans}
    lsum = {c: np.vstack([np.zeros(npix), np.cumsum(np.log(np.where(valid, data[c], 1.0)), axis=0)]) for c in chans}

    flags = np.zeros((k, npix), bool)
    min_p = np.ones(npix)
    start = np.zeros(npix, int)
    active = valid.copy()
    nch = len(chans)
    rounds = 0
    while active.any() and (max_changes is None or rounds < max_changes):
        rounds += 1
        idx = np.where(active)[0]
        s = start[idx]
        m = (k - s).astype(float)
        enough = m >= 2
        z_tot = np.zeros(idx.size)
        om_tot = np.zeros(idx.size)
        for c in chans:
            tot = csum[c][k, idx] - csum[c][s, idx]
            lg = lsum[c][k, idx] - lsum[c][s, idx]
            z, _, om = _omnibus_z(lg, tot, np.maximum(m, 2), n)
            z_tot += z
            om_tot += om
        p_q = _pvalue(z_tot, nch * (np.maximum(m, 2) - 1), om_tot / nch)
        sig = enough & (p_q < alpha)
        active[idx[~sig]] = False
        if not sig.any():
            break
        idx = idx[sig]
        s = start[idx]
        found = np.full(idx.size, -1)
        for jj in range(1, k):  # absolute image index being tested
            cand = (found < 0) & (jj > s)
            if not cand.any():
                continue
            ci = idx[cand]
            sc = s[cand]
            jrel = (jj - sc + 1).astype(float)  # images in segment up to and including jj
            zt = np.zeros(ci.size)
            omt = np.zeros(ci.size)
            for c in chans:
                prev = csum[c][jj, ci] - csum[c][sc, ci]
                cur = data[c][jj, ci]
                z, _, om = _rj_z(prev, cur, prev + cur, jrel, n)
                zt += z
                omt += om
            p_r = _pvalue(zt, np.full(ci.size, float(nch)), omt / nch)
            hit = p_r < alpha
            sel = np.where(cand)[0][hit]
            found[sel] = jj
            min_p[ci[hit]] = np.minimum(min_p[ci[hit]], p_r[hit])
        got = found >= 0
        flags[found[got], idx[got]] = True
        start[idx[got]] = found[got]
        active[idx[~got]] = False

    count = flags.sum(axis=0).astype(int)
    first = np.where(count > 0, np.argmax(flags, axis=0), -1)
    last = np.where(count > 0, k - 1 - np.argmax(flags[::-1], axis=0), -1)
    count = np.where(valid, count, -1)
    return {
        "change_count": count.reshape(h, w),
        "first_change": first.reshape(h, w),
        "last_change": last.reshape(h, w),
        "change_flags": flags.reshape(k, h, w),
        "min_p": min_p.reshape(h, w),
        "enl": n,
        "alpha": alpha,
    }


def to_db(x: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(x, EPS))
