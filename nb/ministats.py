"""Dependency-free replacements for the scipy.stats functions used by
mtdp_sim.py. Implemented directly against standard definitions so the
analysis is reproducible without scipy.

Validated against textbook worked examples in test_ministats.py.
"""
import math
from collections import namedtuple

import numpy as np

MWU = namedtuple("MannwhitneyuResult", "statistic pvalue")
Spearman = namedtuple("SpearmanrResult", "statistic pvalue")
Linreg = namedtuple("LinregressResult", "slope intercept rvalue pvalue stderr")


def _norm_sf(z):
    """Upper-tail probability of the standard normal distribution."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _student_t_sf(t, df):
    """Upper-tail probability of Student's t via the regularised
    incomplete beta function I_x(a, b) with a continued-fraction expansion."""
    t = abs(float(t))
    x = df / (df + t * t)
    return 0.5 * _betainc(df / 2.0, 0.5, x)


def _betainc(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def _betacf(a, b, x, itmax=300, eps=3e-16):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _rankdata(a):
    """Average ranks, ties handled as in scipy.stats.rankdata."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sorted_a = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def mannwhitneyu(x, y, alternative="two-sided"):
    """Mann-Whitney U with tie correction and normal approximation."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n1, n2 = len(x), len(y)
    ranks = _rankdata(np.concatenate([x, y]))
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    mu = n1 * n2 / 2.0
    _, counts = np.unique(np.concatenate([x, y]), return_counts=True)
    n = n1 + n2
    tie_term = (counts ** 3 - counts).sum()
    sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1))))
    if sigma == 0:
        return MWU(u1, 1.0)
    u = max(u1, u2)
    z = (u - mu - 0.5) / sigma            # continuity correction
    if alternative == "two-sided":
        p = min(1.0, 2.0 * _norm_sf(abs(z)))
    elif alternative == "greater":
        p = _norm_sf((u1 - mu - 0.5) / sigma)
    else:
        p = _norm_sf((u2 - mu - 0.5) / sigma)
    return MWU(u1, p)


def spearmanr(x, y):
    rx, ry = _rankdata(x), _rankdata(y)
    n = len(rx)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denom = math.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    if denom == 0:
        return Spearman(0.0, 1.0)
    rho = float((rx * ry).sum() / denom)
    if n <= 2 or abs(rho) >= 1.0:
        return Spearman(rho, 0.0 if abs(rho) >= 1.0 else 1.0)
    t = rho * math.sqrt((n - 2) / (1.0 - rho ** 2))
    return Spearman(rho, min(1.0, 2.0 * _student_t_sf(t, n - 2)))


def linregress(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n = len(x)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    sxy = ((x - xm) * (y - ym)).sum()
    syy = ((y - ym) ** 2).sum()
    slope = sxy / sxx if sxx else 0.0
    intercept = ym - slope * xm
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0
    if n > 2 and abs(r) < 1.0:
        t = r * math.sqrt((n - 2) / (1.0 - r ** 2))
        p = min(1.0, 2.0 * _student_t_sf(t, n - 2))
        se = math.sqrt((1.0 - r ** 2) * syy / (n - 2) / sxx) if sxx else 0.0
    else:
        p, se = (0.0 if abs(r) >= 1.0 else 1.0), 0.0
    return Linreg(slope, intercept, r, p, se)


def mean(xs):
    """Arithmetic mean; returns None for an empty sample rather than raising,
    because a campaign cell can legitimately have zero valid reps."""
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def stdev(xs, ddof=1):
    """Sample standard deviation. Returns 0.0 for a single observation."""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    return float(np.std(xs, ddof=ddof))


def spearman(x, y):
    """Convenience wrapper returning only rho."""
    return spearmanr(x, y).statistic


def holm_bonferroni(pvalues, alpha=0.05):
    """Holm-Bonferroni step-down adjustment.

    Returns adjusted p-values in the ORIGINAL input order. Adjusted values are
    enforced monotonically non-decreasing along the sorted sequence, which is
    what makes the familywise error rate guarantee hold.
    """
    del alpha  # comparison against alpha is the caller's job
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * pvalues[idx]
        running = max(running, min(1.0, val))
        adjusted[idx] = running
    return adjusted


def pca_explained_variance(columns):
    """Explained-variance ratio of the correlation-matrix eigenvalues.

    Standardises each column first, so metrics on wildly different scales
    (IR ~ 0.2, BR ~ 65) contribute comparably.
    """
    m = np.asarray(columns, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        return []
    m = m - m.mean(axis=0)
    sd = m.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    m = m / sd
    cov = np.cov(m, rowvar=False)
    eig = np.linalg.eigvalsh(cov)[::-1]
    eig = np.clip(eig, 0.0, None)
    total = eig.sum()
    return [float(e / total) for e in eig] if total else []


class stats:  # namespace shim so `from ministats import stats as sps` works
    mannwhitneyu = staticmethod(mannwhitneyu)
    spearmanr = staticmethod(spearmanr)
    linregress = staticmethod(linregress)
    rankdata = staticmethod(_rankdata)
