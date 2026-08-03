#!/usr/bin/env python3
"""Aggregate raw run output into the paper's result tables.

Hard rule enforced here: records tagged provenance="model" and
provenance="physical-cluster" are NEVER combined into one table. Mixing a
modelled number with a measured one in a single cell would make the paper
unfalsifiable, so this refuses to do it.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

from nb import ministats
from nb.isolation import blast_radius, fairness_deviation, interference_ratio, jain_index

DATAPATHS = ("DP-IPT", "DP-IPVS", "DP-EBPF")


def load_runs(indir):
    """Load every meta.json + generator/harvester output under `indir`."""
    runs = []
    for meta_path in sorted(pathlib.Path(indir).glob("*/meta.json")):
        cell = meta_path.parent
        rec = json.loads(meta_path.read_text())
        rec["tenants"] = []
        for f in sorted(cell.glob("gen-*.json")):
            try:
                rec["tenants"].append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                sys.stderr.write("skipping unparseable %s\n" % f)
        rec["nodes"] = []
        for f in sorted(cell.glob("harv-*.json")):
            for line in f.read_text().splitlines():
                if line.strip():
                    try:
                        rec["nodes"].append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        runs.append(rec)
    return runs


def check_provenance(runs):
    kinds = {r.get("provenance", "unknown") for r in runs}
    if len(kinds) > 1:
        raise SystemExit(
            "refusing to analyse mixed provenance in one campaign: %s. "
            "Model output and physical measurements must be analysed separately."
            % sorted(kinds)
        )
    return kinds.pop() if kinds else "unknown"


def _key(r):
    return (r["datapath"], r["density"], r["profile"])


def isolation_table(runs):
    """W3 only: IR / FD / BR per (datapath, density), aggregated over reps."""
    out = {}
    grouped = collections.defaultdict(list)
    for r in runs:
        if r["profile"] == "W3":
            grouped[(r["datapath"], r["density"])].append(r)

    for (dp, density), reps in sorted(grouped.items()):
        irs, fds, brs, jains = [], [], [], []
        for r in reps:
            base, aggr, requested, achieved, aggressors = {}, {}, {}, {}, set()
            for t in r["tenants"]:
                tid = t["tenant"]
                if t.get("role") == "aggressor":
                    aggressors.add(tid)
                base[tid] = t.get("quiescent_p99_ms")
                aggr[tid] = t.get("aggression_p99_ms")
                requested[tid] = t.get("target_rps")
                achieved[tid] = t.get("achieved_rps")
            victims = {k: v for k, v in base.items() if k not in aggressors}
            if not victims:
                continue
            b = [base[k] for k in victims if base.get(k)]
            a = [aggr[k] for k in victims if base.get(k)]
            if b and a:
                irs.append(interference_ratio(a, b))
            req = [requested[k] for k in victims if requested.get(k)]
            ach = [achieved[k] for k in victims if requested.get(k)]
            if req:
                fds.append(fairness_deviation(ach, req))
                jains.append(jain_index([x / y for x, y in zip(ach, req)]))
            # blast_radius() expects the documented record shape, not a tuple:
            # {tenant: {"p99_baseline": ..., "p99_aggressed": ...}}
            per_tenant = {
                k: {"p99_baseline": base[k], "p99_aggressed": aggr[k]}
                for k in victims if base.get(k) and aggr.get(k)
            }
            if per_tenant:
                brs.append(blast_radius(per_tenant, aggressors) * 100.0)

        out["%s|%d" % (dp, density)] = {
            "datapath": dp,
            "density": density,
            "reps": len(reps),
            "IR": ministats.mean(irs), "IR_sd": ministats.stdev(irs),
            "FD": ministats.mean(fds), "FD_sd": ministats.stdev(fds),
            "BR": ministats.mean(brs), "BR_sd": ministats.stdev(brs),
            "jain": ministats.mean(jains),
        }
    return out


def significance(runs, metric="IR"):
    """Pairwise DP-EBPF vs others, Mann-Whitney U with Holm-Bonferroni."""
    iso = isolation_table(runs)
    tests, labels = [], []
    densities = sorted({v["density"] for v in iso.values()})
    for d in densities:
        ebpf = [v[metric] for v in iso.values()
                if v["density"] == d and v["datapath"] == "DP-EBPF"]
        for other in ("DP-IPT", "DP-IPVS"):
            rest = [v[metric] for v in iso.values()
                    if v["density"] == d and v["datapath"] == other]
            if len(ebpf) >= 1 and len(rest) >= 1:
                tests.append(ministats.mannwhitneyu(ebpf, rest).pvalue)
                labels.append("d%d DP-EBPF vs %s" % (d, other))
    if not tests:
        return {}
    adjusted = ministats.holm_bonferroni(tests)
    return {lab: {"p_raw": t, "p_adj": p, "significant": p < 0.05}
            for lab, t, p in zip(labels, tests, adjusted)}


def main(argv=None):
    p = argparse.ArgumentParser(description="Aggregate MTDP-Bench run output")
    p.add_argument("--indir", default="out")
    p.add_argument("--out", default="out/results.json")
    a = p.parse_args(argv)

    runs = load_runs(a.indir)
    if not runs:
        raise SystemExit("no runs found under %s" % a.indir)
    provenance = check_provenance(runs)

    results = {
        "provenance": provenance,
        "n_runs": len(runs),
        "tables": {"table6_isolation": isolation_table(runs)},
        "significance": significance(runs),
    }
    outp = pathlib.Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(results, indent=2, sort_keys=True))
    print("analysed %d runs (provenance=%s) -> %s" % (len(runs), provenance, outp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
