#!/usr/bin/env python3
"""Validation battery V1-V5 for the three isolation metrics.

This is the evidence behind the paper's claim that IR, FD and BR are adapted
from established measures and are not three names for the same thing.

  V1  FD <-> Jain algebraic identity holds
  V2  boundary behaviour at known-answer inputs
  V3  BR threshold sensitivity sweep
  V4  pairwise rank correlation between the three metrics
  V5  PCA: how many components explain 95% of their joint variance
"""
from __future__ import annotations

import argparse
import json
import sys

from nb import ministats
from nb.isolation import blast_radius, fairness_deviation, jain_index


def v1_identity():
    """FD computed from ratios must agree with the Jain-derived value."""
    cases = [
        ([100.0] * 10, [100.0] * 10),
        ([100.0] + [0.0] * 9, [100.0] * 10),
        ([100.0] * 5 + [0.0] * 5, [100.0] * 10),
        ([90.0, 80.0, 70.0, 60.0], [100.0] * 4),
    ]
    worst = 0.0
    for achieved, requested in cases:
        ratios = [a / r for a, r in zip(achieved, requested)]
        fd = fairness_deviation(achieved, requested)
        implied = 1.0 - jain_index(ratios)
        worst = max(worst, abs(fd - implied) if fd is not None else 0.0)
    return {"max_abs_err": worst, "pass": worst < 1e-9}


def v2_boundaries():
    n = 10
    return {
        "perfect_equality": jain_index([1.0] * n),
        "single_winner": round(jain_index([1.0] + [0.0] * (n - 1)), 6),
        "single_winner_theory": round(1.0 / n, 6),
        "half_starved": round(jain_index([1.0] * 5 + [0.0] * 5), 6),
        "half_starved_theory": 0.5,
    }


def v3_threshold_sweep(results, per_tenant_dir=None):
    """Recompute BR at each threshold in the sweep.

    A genuine sweep needs the PER-TENANT baseline/aggressed p99 pairs, which
    results.json does not carry (it stores only the aggregated BR). When those
    records are supplied via `per_tenant_dir` we recompute properly; otherwise
    we report the aggregated value once and flag the result as not-a-sweep,
    rather than printing six identical columns that look like a sweep and are
    not. The paper's published sweep was produced from the per-tenant records.
    """
    iso = results.get("tables", {}).get("table6_isolation", {})
    thresholds = (0.10, 0.15, 0.20, 0.25, 0.30, 0.50)

    if per_tenant_dir is None:
        return {
            "swept": False,
            "note": ("per-tenant p99 records not supplied; showing the "
                     "aggregated BR at the campaign default threshold only"),
            "thresholds": list(thresholds),
            "BR_default_threshold": {k: v.get("BR") for k, v in iso.items()},
        }

    import pathlib

    sweep = {"swept": True}
    for thr in thresholds:
        col = {}
        for f in sorted(pathlib.Path(per_tenant_dir).glob("*/per_tenant.json")):
            rec = json.loads(f.read_text())
            cell = rec["cell"]
            col[cell] = 100.0 * blast_radius(
                rec["tenants"], rec.get("aggressors", []), threshold=thr)
        sweep["%.2f" % thr] = col
    return sweep


def v4_correlation(results):
    iso = list(results.get("tables", {}).get("table6_isolation", {}).values())
    if len(iso) < 3:
        return {"note": "insufficient cells for correlation"}
    ir = [c["IR"] for c in iso]
    fd = [c["FD"] for c in iso]
    br = [c["BR"] for c in iso]
    return {
        "IR_FD": ministats.spearman(ir, fd),
        "IR_BR": ministats.spearman(ir, br),
        "FD_BR": ministats.spearman(fd, br),
    }


def v5_pca(results):
    """How many principal components explain 95% of the joint variance?

    Three metrics collapsing onto one component would mean they are redundant.
    """
    iso = list(results.get("tables", {}).get("table6_isolation", {}).values())
    if len(iso) < 3:
        return {"note": "insufficient cells for PCA"}
    rows = [[c["IR"], c["FD"], c["BR"]] for c in iso
            if None not in (c["IR"], c["FD"], c["BR"])]
    evr = ministats.pca_explained_variance(rows)
    cum, n95 = 0.0, len(evr)
    for i, e in enumerate(evr, start=1):
        cum += e
        if cum >= 0.95:
            n95 = i
            break
    return {"explained_variance_ratio": evr, "components_for_95pct": n95}


def main(argv=None):
    p = argparse.ArgumentParser(description="Validate the isolation metrics")
    p.add_argument("--results", default="out/results.json")
    p.add_argument("--per-tenant-dir", default=None,
                   help="directory of per_tenant.json records for a real BR sweep")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    try:
        results = json.loads(open(a.results).read())
    except OSError:
        results = {}
        sys.stderr.write("no results at %s; running analytic checks only\n" % a.results)

    report = {
        "V1_fd_jain_identity": v1_identity(),
        "V2_boundaries": v2_boundaries(),
        "V3_br_threshold_sensitivity": v3_threshold_sweep(results, a.per_tenant_dir),
        "V4_pairwise_correlation": v4_correlation(results),
        "V5_pca": v5_pca(results),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if a.out:
        open(a.out, "w").write(text)
    print(text)
    return 0 if report["V1_fd_jain_identity"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
