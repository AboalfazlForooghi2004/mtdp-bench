"""The three isolation metrics used in the paper: IR, FD, BR.

None of these are inventions. Each is an adaptation of an established
construct, and the paper says so explicitly (Section 3.4.1):

  FD  Fairness Deviation      = 1 - Jain's fairness index (Jain et al., 1984),
                                computed over achieved/requested goodput.
  IR  Interference Ratio      = normalized slowdown, a construct from the
                                datacenter co-location literature, applied to
                                p99 latency of a designated victim archetype.
  BR  Blast Radius            = SLO-violation ratio, applied to *relative*
                                cross-tenant degradation at a swept threshold.

The value added here is the combination and the normalisation choices, not the
mathematics. Keep it that way in any future edit.

All three are pure functions of measured samples. No fitting, no smoothing.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

__all__ = [
    "jain_index",
    "fairness_deviation",
    "interference_ratio",
    "blast_radius",
]


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def jain_index(xs: Iterable[float]) -> float:
    """Jain's fairness index: (sum x)^2 / (n * sum x^2), in (0, 1].

    Equals 1.0 under perfect equality and 1/n when a single tenant takes
    everything. Defined for non-negative allocations only.
    """
    v = [float(x) for x in xs]
    if not v:
        raise ValueError("jain_index requires at least one allocation")
    if any(x < 0 for x in v):
        raise ValueError("jain_index requires non-negative allocations")
    s = sum(v)
    if s == 0:
        # All tenants got exactly nothing. That is perfectly equal, and
        # returning 0 here would wrongly read as maximal unfairness.
        return 1.0
    return (s * s) / (len(v) * sum(x * x for x in v))


def fairness_deviation(achieved: Sequence[float],
                       requested: Sequence[float]) -> float:
    """FD = 1 - Jain(achieved_i / requested_i). Range [0, 1 - 1/n].

    Normalising by the *requested* rate is what makes this a fairness measure
    rather than a size measure: a tenant that asked for little and got little
    is being treated fairly, and must not be scored as starved.
    """
    if len(achieved) != len(requested):
        raise ValueError("achieved and requested must be the same length")
    if not achieved:
        raise ValueError("fairness_deviation requires at least one tenant")
    ratios = []
    for a, r in zip(achieved, requested):
        if r <= 0:
            # A tenant that requested nothing carries no fairness information.
            continue
        ratios.append(min(float(a) / float(r), 1.0))
    if not ratios:
        raise ValueError("no tenant had a positive requested rate")
    return 1.0 - jain_index(ratios)


def interference_ratio(victim_p99_aggressed: Sequence[float],
                       victim_p99_baseline: Sequence[float]) -> float:
    """IR = median over victims of (p99_agg - p99_base) / p99_base.

    0.0 means the aggressor had no measurable effect on the victims; 1.0 means
    victim tail latency doubled. Median rather than mean because a single
    pathologically-placed victim otherwise dominates the statistic.

    Known weakness, stated as limitation L7 in the paper: this is a *relative*
    measure, so a datapath with an already-bad baseline can post a flattering
    IR. Always report absolute victim p99 alongside it.
    """
    if len(victim_p99_aggressed) != len(victim_p99_baseline):
        raise ValueError("aggressed and baseline must be the same length")
    if not victim_p99_aggressed:
        raise ValueError("interference_ratio requires at least one victim")
    deltas = []
    for agg, base in zip(victim_p99_aggressed, victim_p99_baseline):
        if base <= 0:
            raise ValueError("baseline p99 must be positive")
        deltas.append((float(agg) - float(base)) / float(base))
    return _median(deltas)


def blast_radius(per_tenant: Mapping[str, Mapping[str, float]],
                 aggressors: Iterable[str],
                 threshold: float = 0.20) -> float:
    """BR = fraction of NON-aggressor tenants whose p99 degraded by >threshold.

    Expects per_tenant[tenant] = {"p99_baseline": x, "p99_aggressed": y}.
    Returns a value in [0, 1]. The aggressors themselves are excluded: they are
    the cause, and counting them inflates the metric by a constant.

    The 0.20 default is a convention, not a law. The paper sweeps it over
    {0.10 ... 0.50} and reports the ordering's sensitivity, because a metric
    whose ranking flips with an arbitrary constant is not a finding.
    """
    if not 0.0 < threshold < 10.0:
        raise ValueError("threshold must be a positive relative degradation")
    agg = set(aggressors)
    victims = [t for t in per_tenant if t not in agg]
    if not victims:
        raise ValueError("blast_radius requires at least one non-aggressor")
    hit = 0
    for t in victims:
        rec = per_tenant[t]
        base = float(rec["p99_baseline"])
        if base <= 0:
            raise ValueError(f"tenant {t}: baseline p99 must be positive")
        if (float(rec["p99_aggressed"]) - base) / base > threshold:
            hit += 1
    return hit / len(victims)
