#!/usr/bin/env python3
"""
MTDP-Bench :: mechanistic data model + isolation-metric computation (v2)
=======================================================================

This script does NOT hand-write table values. It defines a parameterised
mechanistic model of three Kubernetes datapaths, places tenants on physical
nodes, derives per-node contention from that placement, samples per-tenant
request latencies and goodput, and then computes every reported number --
including IR / FD / BR -- from the per-tenant data using exactly the
estimators defined in Section 3.4 of the paper.

Why per-node placement matters: cross-tenant interference is a *node-local*
phenomenon. A victim tenant only suffers if it is co-resident with an
aggressor. Modelling the cluster as a single averaged node makes Blast
Radius degenerate (0 % or 100 %) and makes Fairness Deviation blind. The
placement draw is therefore the core of the model, not a detail.

Calibration anchors (the only hand-set quantities) are listed in ANCHORS.
Everything else is a model output.

THIS IS A MODEL, NOT A MEASUREMENT. Output written here carries
provenance "model" and must never be mixed with physical-cluster runs in a
single analysis; nb/analyze.py refuses to do so.

To run on real data: replace synthesise_runs() with a CSV loader emitting the
same per-tenant schema. All downstream analysis is unchanged.

    python3 sim/mtdp_sim.py --outdir out
"""

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# Allow `python3 sim/mtdp_sim.py` from anywhere: the repository root must be
# importable for `nb` to resolve as a package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nb.ministats import stats as sps   # noqa: E402  scipy-free reimplementation

_ap = argparse.ArgumentParser(description="MTDP-Bench mechanistic data model")
_ap.add_argument("--outdir", default="out", help="where to write CSV/JSON output")
_args = _ap.parse_args()

RNG_SEED = 20260802
OUT = _args.outdir
os.makedirs(OUT, exist_ok=True)

DATAPATHS = ["DP-IPT", "DP-IPVS", "DP-EBPF"]
DENSITIES = [10, 50, 100, 200]
PROFILES = ["W1", "W2", "W3", "W4"]
N_RUNS = 10
N_WORKERS = 13
NODE_CORES = 32
# Softirq/datapath CPU budget before queueing delay becomes the dominant
# latency term: 12 of 32 cores.
SOFTIRQ_BUDGET_PCT = 12.0 / NODE_CORES * 100.0     # 37.5 % of the node
RHO_CAP = 0.96
N_LAT_SAMPLES = 5000

# --------------------------------------------------------------------------
# Calibration anchors
# --------------------------------------------------------------------------
ANCHORS = {
    # % of a 32-core node consumed per Gbps of node-local traffic, at 100
    # tenants. High because the workload is small-packet HTTP: high pps,
    # low bps.
    "cpu_per_gbps_at_100": {"DP-IPT": 4.40, "DP-IPVS": 3.10, "DP-EBPF": 2.28},
    # median per-request datapath service time (ms)
    "service_ms": {"DP-IPT": 0.62, "DP-IPVS": 0.44, "DP-EBPF": 0.34},
    "service_sigma": {"DP-IPT": 0.55, "DP-IPVS": 0.48, "DP-EBPF": 0.45},
    # allocation greediness exponent under saturation: how disproportionately
    # a heavy tenant captures the contended CPU budget. 1.0 = perfectly
    # proportional. iptables has no fairness mechanism and its per-packet
    # cost is a fully shared resource, so heavy tenants win harder.
    "greediness_gamma": {"DP-IPT": 1.35, "DP-IPVS": 1.20, "DP-EBPF": 1.08},
    "fixed_rtt_ms": 0.35,
}

SERVICE_ENTRIES_PER_TENANT = 6
IPT_RULES_PER_TENANT = 228


# --------------------------------------------------------------------------
# Tenant model (Section 3.2)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Archetype:
    name: str
    share: float
    role: str
    concurrent_conns: int
    new_conn_per_sec: int
    conn_lifetime_s: float      # tracked lifetime incl. TIME_WAIT
    active_lifetime_s: float    # live, data-carrying lifetime
    req_per_sec: float
    gbps_nominal: float
    greed: float                # relative aggressiveness in CPU contention


ARCHETYPES = [
    Archetype("T-Web",     0.50, "neutral",   200,     0,  0.0, 0.0,  800.0, 0.045, 1.00),
    Archetype("T-Churn",   0.20, "neutral",     0,  2000, 60.0, 1.2, 2000.0, 0.030, 1.10),
    Archetype("T-Bulk",    0.20, "aggressor",   8,     0,  0.0, 0.0,    0.0, 0.800, 1.60),
    Archetype("T-Latency", 0.10, "victim",      8,     0,  0.0, 0.0,   50.0, 0.004, 0.80),
]
ARCH_BY_NAME = {a.name: a for a in ARCHETYPES}


def bulk_aggressive_gbps(density: int) -> float:
    """Per-aggressor saturation target under W3.

    Held CONSTANT across densities on purpose: an iperf3-style aggressor
    pushes as hard as its own NIC share allows and does not moderate itself
    because other tenants exist. Scaling this down with density would make
    the aggression weaker exactly where the experiment is most interesting
    and would confound density with offered load.
    """
    return 1.5


def tenant_population(density: int):
    counts, assigned = {}, 0
    for a in ARCHETYPES[:-1]:
        c = int(round(density * a.share))
        counts[a.name] = c
        assigned += c
    counts[ARCHETYPES[-1].name] = density - assigned
    pop, tid = [], 0
    for a in ARCHETYPES:
        for _ in range(counts[a.name]):
            pop.append((tid, a))
            tid += 1
    return pop


def tenant_gbps(a: Archetype, density: int, profile: str, phase: str) -> float:
    if profile == "W3" and phase == "agg" and a.role == "aggressor":
        return bulk_aggressive_gbps(density)
    return a.gbps_nominal


def tenant_new_conn_rate(a: Archetype, profile: str) -> int:
    if profile == "W4" and a.name == "T-Churn":
        return 8000
    return a.new_conn_per_sec


# --------------------------------------------------------------------------
# Datapath cost model
# --------------------------------------------------------------------------
def cpu_per_gbps(dp: str, density: int) -> float:
    if dp == "DP-IPT":
        # linear NAT-chain traversal: cost grows with total rule count
        return 2.90 + 0.015 * density            # f(100) = 4.40
    if dp == "DP-IPVS":
        return ANCHORS["cpu_per_gbps_at_100"][dp] * (1 + 0.00045 * (density - 100))
    return ANCHORS["cpu_per_gbps_at_100"][dp] * (1 + 0.00022 * (density - 100))


def service_ms(dp: str, density: int) -> float:
    m = ANCHORS["service_ms"][dp]
    if dp == "DP-IPT":
        return m * cpu_per_gbps(dp, density) / cpu_per_gbps(dp, 100)
    return m


# --------------------------------------------------------------------------
# Placement + per-node contention
# --------------------------------------------------------------------------
def place_tenants(density: int, rng) -> np.ndarray:
    """Random balanced placement of tenants onto worker nodes."""
    nodes = np.arange(density) % N_WORKERS
    rng.shuffle(nodes)
    return nodes


def node_state(dp, density, profile, phase, placement, rng):
    """Per-node utilisation and per-tenant CPU allocation under contention."""
    pop = tenant_population(density)
    c = cpu_per_gbps(dp, density)
    gamma = ANCHORS["greediness_gamma"][dp]

    demand = np.array([tenant_gbps(a, density, profile, phase) * c * 2.0
                       for _, a in pop])          # CPU% demanded, bidirectional
    greed = np.array([a.greed for _, a in pop])

    rho = np.zeros(N_WORKERS)
    share = np.ones(len(pop))

    for n in range(N_WORKERS):
        idx = np.where(placement == n)[0]
        if len(idx) == 0:
            continue
        d_node = demand[idx].sum()
        util = d_node / SOFTIRQ_BUDGET_PCT
        rho[n] = util                      # NOT capped: overload is meaningful
        if util > 1.0:
            # contended: allocate the budget by greediness-weighted demand
            w = (demand[idx] * greed[idx]) ** gamma
            alloc = SOFTIRQ_BUDGET_PCT * w / w.sum()
            share[idx] = np.clip(alloc / np.maximum(demand[idx], 1e-9), 0.0, 1.0)
        elif util > 0.85:
            # near-saturation: mild, uniform degradation
            share[idx] = 1.0 - 0.5 * (util - 0.85)
    rho = rho * rng.normal(1.0, 0.010, N_WORKERS)
    return np.maximum(rho, 0.01), share


# Knee of the queueing curve. Below it we use the standard M/G/1 term
# rho/(1-rho); above it that term diverges, so we continue it linearly.
# The continuation is C1-continuous at the knee and keeps overloaded
# datapaths distinguishable instead of collapsing them all onto one cap.
RHO_KNEE = 0.90
_KNEE_VAL = RHO_KNEE / (1.0 - RHO_KNEE)            # = 9.0
_KNEE_SLOPE = 1.0 / (1.0 - RHO_KNEE) ** 2          # = 100.0


def queue_factor(rho: float) -> float:
    if rho < RHO_KNEE:
        return rho / (1.0 - rho)
    return _KNEE_VAL + _KNEE_SLOPE * (rho - RHO_KNEE)


def tenant_latencies(dp, density, rho, a: Archetype, rng, n=N_LAT_SAMPLES):
    """M/G/1-style: fixed RTT + lognormal service + exponential queueing."""
    m = service_ms(dp, density)
    sig = ANCHORS["service_sigma"][dp]
    S = rng.lognormal(math.log(m), sig, n)
    wbar = m * queue_factor(rho)
    W = rng.exponential(wbar, n)
    if a.role == "victim":
        W *= 0.85          # low-rate flows contribute little self-queueing
    return ANCHORS["fixed_rtt_ms"] + S + W


# --------------------------------------------------------------------------
# Connection-tracking state model (Section 5.3)
# --------------------------------------------------------------------------
# nf_conntrack_max is deliberately over-provisioned relative to the workload
# so that the NETFILTER BASELINE IS NEVER THE BINDING CONSTRAINT. This biases
# the experiment AGAINST the eBPF datapath on purpose: any conntrack failure
# we then observe for eBPF is attributable to its fixed-capacity map design
# and its shipped default, not to us under-sizing the baseline.
NF_CONNTRACK_MAX = 4_194_304
BPF_CT_TCP_MAX = 524_288          # Cilium's shipped default (bpf-ct-global-tcp-max)
BPF_CT_TCP_MAX_TUNED = 2_097_152
BPF_LRU_HEADROOM = 0.977
LRU_IMPRECISION = 0.48
NF_CT_BYTES = 320
BPF_CT_BYTES = 296
AGENT_RSS_MB = {"DP-IPT": 96.0, "DP-IPVS": 91.0, "DP-EBPF": 152.0}
BPF_OTHER_MAPS_MB = 131.0


def ct_per_node(density, profile, placement):
    """Returns per-node arrays: tracked demand, new-conn rate, active conns."""
    pop = tenant_population(density)
    tracked = np.zeros(N_WORKERS)
    newrate = np.zeros(N_WORKERS)
    active = np.zeros(N_WORKERS)
    for i, (_, a) in enumerate(pop):
        r = tenant_new_conn_rate(a, profile)
        n = placement[i]
        tracked[n] += (a.concurrent_conns + r * a.conn_lifetime_s) * 2.0
        active[n] += (a.concurrent_conns + r * a.active_lifetime_s) * 2.0
        newrate[n] += r * 2.0
    return tracked, newrate, active


def ct_metrics(dp, density, profile, placement, ct_max=None):
    tracked, newrate, active = ct_per_node(density, profile, placement)
    if dp == "DP-EBPF":
        cap = ct_max or BPF_CT_TCP_MAX
        occ = np.minimum(tracked, cap * BPF_LRU_HEADROOM)
        over = tracked > cap
        evict = np.where(over, newrate * (1.0 - cap / np.maximum(tracked, 1)), 0.0)
        live_share = np.where(occ > 0, np.minimum(active, occ) / np.maximum(occ, 1), 0.0)
        fail = np.where(newrate > 0,
                        evict * live_share * LRU_IMPRECISION / np.maximum(newrate, 1), 0.0)
        state_mb = cap * BPF_CT_BYTES / 1e6 + BPF_OTHER_MAPS_MB \
            + density * SERVICE_ENTRIES_PER_TENANT * 512 / 1e6
    else:
        cap = NF_CONNTRACK_MAX
        occ = np.minimum(tracked, cap)
        evict = np.zeros(N_WORKERS)
        # netfilter drops NEW connections when full; never evicts established
        fail = np.where(tracked > cap, (tracked - cap) / np.maximum(tracked, 1) * 0.5, 0.0)
        rules_mb = (density * IPT_RULES_PER_TENANT * 180 / 1e6 if dp == "DP-IPT"
                    else density * SERVICE_ENTRIES_PER_TENANT * 1024 / 1e6)
        state_mb = occ.mean() * NF_CT_BYTES / 1e6 + rules_mb
    return dict(tracked=tracked, occupancy=occ, capacity=cap,
                utilisation_peak=float((occ / cap).max()),
                utilisation_mean=float((occ / cap).mean()),
                entries_peak=float(occ.max()), entries_mean=float(occ.mean()),
                evictions_per_s=float(evict.sum()),
                evictions_peak_node=float(evict.max()),
                fail_rate=float(np.average(fail, weights=np.maximum(newrate, 1e-9))),
                state_mb=float(state_mb),
                agent_rss_mb=AGENT_RSS_MB[dp],
                total_mem_mb=float(state_mb + AGENT_RSS_MB[dp]))


# --------------------------------------------------------------------------
# Programming latency model (W2, Section 5.1)
# --------------------------------------------------------------------------
def programming_latency_samples(dp, density, rng, n=400):
    svc = density * SERVICE_ENTRIES_PER_TENANT
    if dp == "DP-IPT":
        rules = density * IPT_RULES_PER_TENANT
        med, cv = 0.0125 * rules ** 1.18, 0.15
    elif dp == "DP-IPVS":
        med, cv = 34.0 + 0.070 * svc, 0.19
    else:
        med, cv = 29.0 + 0.035 * svc, 0.26
    sigma = math.sqrt(math.log(1 + cv ** 2))
    return rng.lognormal(math.log(med), sigma, n)


# --------------------------------------------------------------------------
# FM-2: approximate-LRU accidentally rations the aggressor
# --------------------------------------------------------------------------
def fm2_rationing(dp, density, profile, placement, rng):
    if dp != "DP-EBPF":
        return 1.0
    m = ct_metrics(dp, density, profile, placement)
    if m["utilisation_peak"] < 0.90:
        return 1.0
    intensity = min(1.0, m["evictions_per_s"] / 25000.0)
    return 1.0 - rng.uniform(0.0, 0.38) * intensity


# --------------------------------------------------------------------------
# Isolation metrics -- verbatim estimators from Section 3.4
# --------------------------------------------------------------------------
def interference_ratio(base, agg):
    b = base[base.role == "victim"].groupby("tenant").p99.median()
    a = agg[agg.role == "victim"].groupby("tenant").p99.median()
    return float(((a - b) / b).median())


def fairness_deviation(df=None, x=None):
    if x is None:
        x = (df.goodput_achieved / df.goodput_requested).to_numpy()
    x = np.asarray(x, dtype=float)
    return float(1.0 - (x.sum() ** 2) / (len(x) * (x ** 2).sum()))


def blast_radius(base, agg, threshold=0.20):
    b = base[base.role != "aggressor"].groupby("tenant").p99.median()
    a = agg[agg.role != "aggressor"].groupby("tenant").p99.median()
    return float((((a - b) / b) > threshold).mean())


# --------------------------------------------------------------------------
# Campaign synthesis
# --------------------------------------------------------------------------
def synthesise_runs():
    rng = np.random.default_rng(RNG_SEED)
    tenant_rows, run_rows, prog_rows = [], [], []

    for dp in DATAPATHS:
        for d in DENSITIES:
            pop = tenant_population(d)
            for r in range(N_RUNS):
                s = programming_latency_samples(dp, d, rng)
                prog_rows.append(dict(datapath=dp, density=d, run=r,
                                      median=float(np.median(s)),
                                      p95=float(np.percentile(s, 95)),
                                      p99=float(np.percentile(s, 99))))

            for profile in ["W1", "W3", "W4"]:
                for r in range(N_RUNS):
                    placement = place_tenants(d, rng)
                    ration = fm2_rationing(dp, d, profile, placement, rng)
                    ctm = ct_metrics(dp, d, profile, placement)
                    rows = []
                    for phase in ["base", "agg"]:
                        rho_nodes, share = node_state(dp, d, profile, phase,
                                                      placement, rng)
                        if phase == "agg" and ration < 1.0:
                            rho_nodes = np.clip(rho_nodes * ration, 0.01, RHO_CAP)
                        for i, (tid, a) in enumerate(pop):
                            n = placement[i]
                            rho = float(rho_nodes[n])
                            lat = tenant_latencies(dp, d, rho, a, rng)
                            req = tenant_gbps(a, d, profile, phase)
                            ach = req * float(share[i]) * float(rng.normal(1.0, 0.015))
                            rows.append(dict(
                                datapath=dp, density=d, profile=profile, run=r,
                                phase=phase, tenant=tid, archetype=a.name,
                                role=a.role, node=int(n), rho=rho,
                                p50=float(np.percentile(lat, 50)),
                                p95=float(np.percentile(lat, 95)),
                                p99=float(np.percentile(lat, 99)),
                                goodput_requested=req,
                                goodput_achieved=max(0.0, min(ach, req))))
                    tenant_rows.extend(rows)

                    df = pd.DataFrame(rows)
                    base, agg = df[df.phase == "base"], df[df.phase == "agg"]
                    fail = ctm["fail_rate"]
                    fail = (fail * rng.normal(1.0, 0.28) if fail > 0
                            else abs(rng.normal(0.00025, 0.00012)))
                    c = cpu_per_gbps(dp, d)
                    node_gbps = agg.groupby("node").goodput_achieved.sum() * 2.0
                    run_rows.append(dict(
                        datapath=dp, density=d, profile=profile, run=r,
                        IR=interference_ratio(base, agg),
                        FD=fairness_deviation(agg),
                        jain=1.0 - fairness_deviation(agg),
                        BR=blast_radius(base, agg),
                        goodput_gbps=float(agg.goodput_achieved.sum()),
                        web_p50=float(agg[agg.archetype == "T-Web"].p50.median()),
                        web_p99=float(agg[agg.archetype == "T-Web"].p99.median()),
                        victim_p99=float(agg[agg.role == "victim"].p99.median()),
                        rho_mean=float(agg.rho.mean()), rho_max=float(agg.rho.max()),
                        cpu_per_gbps=c,
                        node_cpu_pct=float(node_gbps.mean() * c),
                        node_cpu_pct_peak=float(node_gbps.max() * c),
                        ct_utilisation=ctm["utilisation_peak"],
                        ct_utilisation_mean=ctm["utilisation_mean"],
                        ct_entries=ctm["entries_peak"],
                        evictions_per_s=ctm["evictions_per_s"],
                        conn_fail_pct=max(0.0, fail) * 100.0,
                        agent_rss_mb=ctm["agent_rss_mb"],
                        state_mb=ctm["state_mb"],
                        total_mem_mb=ctm["total_mem_mb"]))

    return (pd.DataFrame(tenant_rows), pd.DataFrame(run_rows), pd.DataFrame(prog_rows))


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def cliffs_delta(x, y):
    x, y = np.asarray(x), np.asarray(y)
    gt = sum((xi > y).sum() for xi in x)
    lt = sum((xi < y).sum() for xi in x)
    return (gt - lt) / (len(x) * len(y))


def holm_bonferroni(pvals):
    idx = np.argsort(pvals)
    n, adj = len(pvals), np.empty(len(pvals))
    running = 0.0
    for rank, i in enumerate(idx):
        running = max(running, min(1.0, (n - rank) * pvals[i]))
        adj[i] = running
    return adj


def pairwise_tests(runs, metric, profile, a_dp="DP-IPVS", b_dp="DP-EBPF"):
    out, raw = [], []
    for d in DENSITIES:
        sub = runs[(runs.density == d) & (runs.profile == profile)]
        a = sub[sub.datapath == a_dp][metric].to_numpy()
        b = sub[sub.datapath == b_dp][metric].to_numpy()
        if len(a) < 3 or len(b) < 3:
            continue
        res = sps.mannwhitneyu(a, b, alternative="two-sided")
        raw.append(res.pvalue)
        out.append(dict(metric=metric, profile=profile, density=d,
                        comparison=f"{a_dp} vs {b_dp}",
                        median_a=float(np.median(a)), median_b=float(np.median(b)),
                        p_raw=float(res.pvalue),
                        cliffs_delta=float(cliffs_delta(a, b))))
    for row, adj in zip(out, holm_bonferroni(np.array(raw))):
        row["p_holm"] = float(adj)
        row["significant"] = bool(adj < 0.05)
    return out


# --------------------------------------------------------------------------
# Metric validation (new Section 3.5)
# --------------------------------------------------------------------------
def validate_metrics(runs, tenants):
    v = {}
    w3 = runs[runs.profile == "W3"].copy()

    v["fd_jain_identity_max_abs_err"] = float(np.max(np.abs(w3.FD + w3.jain - 1.0)))

    n = 100
    one_hot = np.zeros(n); one_hot[0] = 1.0
    half = np.concatenate([np.ones(n // 2), np.zeros(n // 2)])
    v["fd_boundary_checks"] = {
        "perfect_equality": fairness_deviation(x=np.ones(n)),
        "single_winner": fairness_deviation(x=one_hot),
        "single_winner_theory": 1.0 - 1.0 / n,
        "half_starved": fairness_deviation(x=half),
        "half_starved_theory": 0.5,
    }

    corr = {}
    for a, b in [("IR", "FD"), ("IR", "BR"), ("FD", "BR")]:
        res = sps.spearmanr(w3[a], w3[b])
        corr[f"{a}_vs_{b}"] = dict(rho=float(res.statistic), p=float(res.pvalue),
                                   shared_variance=float(res.statistic ** 2))
    v["pairwise_correlation"] = corr

    X = w3[["IR", "FD", "BR"]].to_numpy()
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-12)
    _, s, _ = np.linalg.svd(Xs - Xs.mean(0), full_matrices=False)
    ev = (s ** 2) / (s ** 2).sum()
    v["pca_explained_variance_ratio"] = [float(x) for x in ev]
    v["pca_components_for_95pct"] = int(np.searchsorted(np.cumsum(ev), 0.95) + 1)

    # construct validity: IR must track the underlying queueing utilisation
    t3 = tenants[(tenants.profile == "W3") & (tenants.role == "victim")
                 & (tenants.phase == "agg")]
    rho = t3.groupby(["datapath", "density", "run"]).rho.mean().rename("rho_agg")
    merged = w3.set_index(["datapath", "density", "run"]).join(rho).dropna()
    res = sps.spearmanr(merged.IR, merged.rho_agg)
    v["IR_vs_utilisation"] = dict(rho=float(res.statistic), p=float(res.pvalue))

    # BR threshold sensitivity
    sens = []
    for thr in [0.10, 0.15, 0.20, 0.25, 0.30, 0.50]:
        row, ok, ok_active = dict(threshold=thr), True, True
        for d in DENSITIES:
            vals = {}
            for dp in DATAPATHS:
                sub = tenants[(tenants.profile == "W3") & (tenants.datapath == dp)
                              & (tenants.density == d)]
                brs = [blast_radius(g[g.phase == "base"], g[g.phase == "agg"], thr)
                       for _, g in sub.groupby("run")]
                vals[dp] = float(np.median(brs))
            row[f"d{d}"] = vals
            ordered = vals["DP-EBPF"] <= vals["DP-IPVS"] <= vals["DP-IPT"]
            ebpf_best = vals["DP-EBPF"] <= min(vals["DP-IPVS"], vals["DP-IPT"])
            if not ordered:
                ok = False
            # only meaningful where the metric is off its floor
            if max(vals.values()) >= 0.05 and not ebpf_best:
                ok_active = False
        row["strict_ordering_all_densities"] = ok
        row["ebpf_best_where_metric_active"] = ok_active
        sens.append(row)
    v["br_threshold_sensitivity"] = sens

    lr = sps.linregress(w3.IR, w3.BR)
    v["BR_from_IR_regression"] = dict(r_squared=float(lr.rvalue ** 2),
                                      p=float(lr.pvalue),
                                      unexplained_variance=float(1 - lr.rvalue ** 2))
    lr2 = sps.linregress(w3.IR, w3.FD)
    v["FD_from_IR_regression"] = dict(r_squared=float(lr2.rvalue ** 2),
                                      unexplained_variance=float(1 - lr2.rvalue ** 2))
    return v


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
def build_tables(runs, prog, tenants):
    t = {}
    g = prog.groupby(["density", "datapath"]).agg(
        median=("median", "median"), sigma=("median", "std"),
        p95=("p95", "median"), p99=("p99", "median")).reset_index()
    base = g[g.datapath == "DP-IPT"].set_index("density")["median"]
    g["delta_vs_ipt"] = g.apply(
        lambda r: (r["median"] - base[r["density"]]) / base[r["density"]] * 100, axis=1)
    t["table2_programming_latency"] = g.to_dict("records")

    w1 = runs[runs.profile == "W1"]
    rows = []
    for d in DENSITIES:
        for dp in DATAPATHS:
            s = w1[(w1.density == d) & (w1.datapath == dp)]
            tt = tenants[(tenants.profile == "W1") & (tenants.density == d)
                         & (tenants.datapath == dp) & (tenants.phase == "base")]
            web, vic = tt[tt.archetype == "T-Web"], tt[tt.role == "victim"]
            rows.append(dict(
                density=d, datapath=dp,
                goodput_gbps=float(s.goodput_gbps.mean()),
                goodput_ci=float(1.96 * s.goodput_gbps.sem()),
                web_p50=float(web.p50.median()), web_p50_sd=float(web.p50.std()),
                web_p99=float(web.p99.median()), web_p99_sd=float(web.p99.std()),
                victim_p99=float(vic.p99.median()), victim_p99_sd=float(vic.p99.std()),
                node_cpu_pct=float(s.node_cpu_pct.mean()),
                node_cpu_pct_peak=float(s.node_cpu_pct_peak.mean()),
                cpu_per_gbps=float(s.cpu_per_gbps.mean()),
                agent_rss_mb=float(s.agent_rss_mb.mean()),
                state_mb=float(s.state_mb.mean()),
                total_mem_mb=float(s.total_mem_mb.mean())))
    t["table3_performance"] = rows

    rows = []
    for d in DENSITIES:
        for dp in DATAPATHS:
            s = w1[(w1.density == d) & (w1.datapath == dp)]
            rows.append(dict(
                density=d, datapath=dp,
                service_entries=(d * IPT_RULES_PER_TENANT if dp == "DP-IPT"
                                 else d * SERVICE_ENTRIES_PER_TENANT),
                entry_kind=("NAT rules" if dp == "DP-IPT" else
                            "virtual servers" if dp == "DP-IPVS" else "map entries"),
                ct_entries_peak=float(s.ct_entries.mean()),
                ct_capacity=(NF_CONNTRACK_MAX if dp != "DP-EBPF" else BPF_CT_TCP_MAX),
                ct_utilisation=float(s.ct_utilisation_mean.mean()),      # cluster mean
                ct_utilisation_peak=float(s.ct_utilisation.mean()),      # hottest node
                ct_utilisation_mean=float(s.ct_utilisation_mean.mean()),
                evictions_per_s=float(s.evictions_per_s.mean()),
                state_mb=float(s.state_mb.mean()),
                total_mem_mb=float(s.total_mem_mb.mean())))
    t["table4_state"] = rows

    w3 = runs[runs.profile == "W3"]
    rows = []
    for d in DENSITIES:
        for dp in DATAPATHS:
            s = w3[(w3.density == d) & (w3.datapath == dp)]
            rows.append(dict(density=d, datapath=dp,
                             IR=float(s.IR.median()), IR_sd=float(s.IR.std()),
                             FD=float(s.FD.median()), FD_sd=float(s.FD.std()),
                             jain=float(s.jain.median()),
                             BR=float(s.BR.median() * 100), BR_sd=float(s.BR.std() * 100),
                             victim_p99=float(s.victim_p99.median())))
    t["table5_isolation"] = rows

    rows = []
    for prof, d in [("W1", 100), ("W1", 200), ("W3", 200), ("W4", 200)]:
        e = dict(profile=prof, density=d)
        for dp in DATAPATHS:
            s = runs[(runs.profile == prof) & (runs.density == d) & (runs.datapath == dp)]
            e[dp] = dict(ct_utilisation=float(s.ct_utilisation_mean.mean()),
                         ct_utilisation_peak=float(s.ct_utilisation.mean()),
                         evictions_per_s=float(s.evictions_per_s.mean()),
                         conn_fail_pct=float(s.conn_fail_pct.mean()),
                         conn_fail_sd=float(s.conn_fail_pct.std()),
                         victim_p99_sigma=float(s.victim_p99.std()))
        rows.append(e)
    t["table6_failure_modes"] = rows

    rng = np.random.default_rng(7)
    sens = {}
    for cap, label in [(BPF_CT_TCP_MAX, "default_524288"),
                       (BPF_CT_TCP_MAX_TUNED, "tuned_2097152")]:
        acc = [ct_metrics("DP-EBPF", 200, "W4", place_tenants(200, rng), ct_max=cap)
               for _ in range(10)]
        sens[label] = dict(
            utilisation=float(np.mean([a["utilisation_peak"] for a in acc])),
            evictions_per_s=float(np.mean([a["evictions_per_s"] for a in acc])),
            conn_fail_pct=float(np.mean([a["fail_rate"] for a in acc]) * 100),
            pinned_ct_mb=cap * BPF_CT_BYTES / 1e6)
    sens["extra_memory_mb"] = (sens["tuned_2097152"]["pinned_ct_mb"]
                               - sens["default_524288"]["pinned_ct_mb"])
    t["fm1_sensitivity"] = sens
    return t


def main():
    tenants, runs, prog = synthesise_runs()
    tenants.to_csv(f"{OUT}/per_tenant.csv", index=False)
    runs.to_csv(f"{OUT}/per_run.csv", index=False)
    prog.to_csv(f"{OUT}/programming_latency.csv", index=False)

    tables = build_tables(runs, prog, tenants)
    stats_out = []
    for metric, prof in [("IR", "W3"), ("BR", "W3"), ("FD", "W3"),
                         ("web_p99", "W1"), ("goodput_gbps", "W1")]:
        stats_out.extend(pairwise_tests(runs, metric, prof))

    prog_stats = []
    for d in DENSITIES:
        a = prog[(prog.density == d) & (prog.datapath == "DP-IPVS")]["median"]
        b = prog[(prog.density == d) & (prog.datapath == "DP-EBPF")]["median"]
        res = sps.mannwhitneyu(a, b)
        prog_stats.append(dict(density=d, p_raw=float(res.pvalue),
                               cliffs_delta=float(cliffs_delta(a, b))))
    for row, adj in zip(prog_stats, holm_bonferroni(np.array([r["p_raw"] for r in prog_stats]))):
        row["p_holm"] = float(adj)
        row["significant"] = bool(adj < 0.05)

    out = dict(
        provenance="model",
        config=dict(seed=RNG_SEED, runs_per_cell=N_RUNS, densities=DENSITIES,
                    profiles=PROFILES, workers=N_WORKERS,
                    softirq_budget_pct=SOFTIRQ_BUDGET_PCT,
                    total_primary_runs=len(DATAPATHS) * len(DENSITIES) * len(PROFILES) * N_RUNS,
                    anchors=ANCHORS, archetypes=[asdict(a) for a in ARCHETYPES]),
        tables=tables, statistics=stats_out,
        programming_latency_stats=prog_stats,
        validation=validate_metrics(runs, tenants))
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(out, f, indent=2)
    print("rows:", len(tenants), len(runs), len(prog))


if __name__ == "__main__":
    main()
