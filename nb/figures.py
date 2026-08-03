#!/usr/bin/env python3
"""Publication figures for the MTDP-Bench paper, rendered from the CSV/JSON
emitted by mtdp_sim.py. Grayscale-safe: every series has a distinct marker
and linestyle as well as a distinct colour.

Paths are CLI arguments, not constants, so `make figures` works from a checkout:

    python3 nb/figures.py --indir out --results out/results.json --outdir figs
"""
import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

_ap = argparse.ArgumentParser(description="Render the MTDP-Bench paper figures")
_ap.add_argument("--indir", default="out",
                 help="directory holding per_run.csv / per_tenant.csv / "
                      "programming_latency.csv")
_ap.add_argument("--results", default=None,
                 help="results.json path (default: <indir>/results.json)")
_ap.add_argument("--outdir", default="figs", help="where to write the PNGs")
_args = _ap.parse_args()

OUT = _args.indir
FIG = _args.outdir
RESULTS = _args.results or os.path.join(OUT, "results.json")
os.makedirs(FIG, exist_ok=True)

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "DejaVu Serif"],
    "font.size": 10,
    "axes.labelsize": 10.5,
    "axes.titlesize": 11,
    "legend.fontsize": 9,
    "xtick.labelsize": 9.5,
    "ytick.labelsize": 9.5,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "figure.dpi": 200,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
})

DP = ["DP-IPT", "DP-IPVS", "DP-EBPF"]
LABEL = {"DP-IPT": "kube-proxy / iptables", "DP-IPVS": "kube-proxy / IPVS",
         "DP-EBPF": "Cilium eBPF"}
COLOR = {"DP-IPT": "#B23A3A", "DP-IPVS": "#D08B18", "DP-EBPF": "#2D6E9E"}
MARK = {"DP-IPT": "s", "DP-IPVS": "^", "DP-EBPF": "o"}
LS = {"DP-IPT": "--", "DP-IPVS": "-.", "DP-EBPF": "-"}
HATCH = {"DP-IPT": "//", "DP-IPVS": "\\\\", "DP-EBPF": ""}
DENS = [10, 50, 100, 200]

runs = pd.read_csv(f"{OUT}/per_run.csv")
tenants = pd.read_csv(f"{OUT}/per_tenant.csv")
prog = pd.read_csv(f"{OUT}/programming_latency.csv")
res = json.load(open(RESULTS))


def lineplot(ax, get, err=None, logy=False):
    for dp in DP:
        y = np.array([get(dp, d) for d in DENS], dtype=float)
        kw = dict(color=COLOR[dp], marker=MARK[dp], linestyle=LS[dp],
                  label=LABEL[dp], linewidth=1.6, markersize=6,
                  markerfacecolor="white", markeredgewidth=1.4)
        if err is not None:
            e = np.array([err(dp, d) for d in DENS], dtype=float)
            ax.errorbar(DENS, y, yerr=e, capsize=3, elinewidth=1.0, **kw)
        else:
            ax.plot(DENS, y, **kw)
    ax.set_xscale("log")
    ax.set_xticks(DENS)
    ax.set_xticklabels([str(d) for d in DENS])
    ax.set_xlabel("Tenant count (log scale)")
    if logy:
        ax.set_yscale("log")


def bars(ax, get, ylabel, err=None, fmt="{:.2f}", annotate=True):
    x = np.arange(len(DENS))
    w = 0.26
    for i, dp in enumerate(DP):
        y = [get(dp, d) for d in DENS]
        e = [err(dp, d) for d in DENS] if err else None
        b = ax.bar(x + (i - 1) * w, y, w, label=LABEL[dp], color=COLOR[dp],
                   edgecolor="black", linewidth=0.5, hatch=HATCH[dp],
                   yerr=e, capsize=2.5, error_kw=dict(elinewidth=0.9))
        if annotate:
            for rect, v in zip(b, y):
                ax.annotate(fmt.format(v), (rect.get_x() + rect.get_width() / 2,
                                            rect.get_height()),
                            ha="center", va="bottom", fontsize=7.0,
                            xytext=(0, 1.5), textcoords="offset points")
    ax.set_xticks(x)
    ax.set_xticklabels([str(d) for d in DENS])
    ax.set_xlabel("Tenant count")
    ax.set_ylabel(ylabel)


def save(fig, name):
    fig.savefig(f"{FIG}/{name}.png")
    plt.close(fig)
    print("wrote", name)


P = prog.groupby(["datapath", "density"])
PMED = P["median"].median()
PSD = P["median"].std()
W1 = runs[runs.profile == "W1"].groupby(["datapath", "density"])
W3 = runs[runs.profile == "W3"].groupby(["datapath", "density"])
T1 = tenants[(tenants.profile == "W1") & (tenants.phase == "base")]

# ---------------------------------------------------------------- Figure 1
fig, ax = plt.subplots(figsize=(5.6, 3.5))
lineplot(ax, lambda dp, d: PMED[(dp, d)], lambda dp, d: PSD[(dp, d)], logy=True)
ax.set_ylabel("Endpoint\u2192datapath programming latency (ms)")
ax.set_title("Service programming latency vs tenant count (W2)")
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:g}"))
ax.legend(frameon=False, loc="upper left")
ax.annotate("iptables: full NAT table\nregenerated per sync",
            xy=(200, PMED[("DP-IPT", 200)]), xytext=(52, 1500),
            fontsize=8, color="#B23A3A",
            arrowprops=dict(arrowstyle="->", color="#B23A3A", lw=0.9))
save(fig, "fig1_programming_latency")

# ---------------------------------------------------------------- Figure 2
fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
bars(axes[0], lambda dp, d: W1["cpu_per_gbps"].mean()[(dp, d)],
     "Datapath CPU per Gbps\n(% of a 32-core node)")
axes[0].set_title("(a) Normalised datapath CPU cost")
axes[0].legend(frameon=False, loc="upper left", fontsize=8)
bars(axes[1], lambda dp, d: W1["node_cpu_pct"].mean()[(dp, d)],
     "Mean node datapath CPU (% of node)", fmt="{:.1f}")
axes[1].axhline(37.5, color="black", ls=":", lw=1.2)
axes[1].annotate("softirq budget (12 of 32 cores)", (2.35, 38.6), fontsize=7.5)
axes[1].set_title("(b) Absolute node CPU consumption (W1)")
save(fig, "fig2_cpu_per_gbps")

# ---------------------------------------------------------------- Figure 3
fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
web = T1[T1.archetype == "T-Web"].groupby(["datapath", "density"]).p99
vic = T1[T1.role == "victim"].groupby(["datapath", "density"]).p99
for ax, series, title in [(axes[0], web, "(a) T-Web tenants"),
                          (axes[1], vic, "(b) T-Latency (victim) tenants")]:
    lineplot(ax, lambda dp, d, s=series: s.median()[(dp, d)],
             lambda dp, d, s=series: s.std()[(dp, d)], logy=True)
    ax.set_ylabel("p99 request latency (ms)")
    ax.set_title(title)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:g}"))
axes[0].legend(frameon=False, loc="upper left")
fig.suptitle("Steady-state tail latency vs tenant count (W1)", y=1.02, fontsize=11)
save(fig, "fig3_p99_latency")

# ------------------------------------------------- Figures 4-6: isolation
for name, col, ylab, title, scale in [
    ("fig4_interference_ratio", "IR", "Interference Ratio (median p99 inflation)",
     "Cross-tenant interference under aggressor injection (W3)", 1.0),
    ("fig5_fairness_deviation", "FD", "Fairness Deviation  (1 \u2212 Jain's index)",
     "Goodput fairness degradation under aggressor injection (W3)", 1.0),
    ("fig6_blast_radius", "BR", "Blast Radius (% of tenants >20% degraded)",
     "Spread of interference under aggressor injection (W3)", 100.0),
]:
    fig, ax = plt.subplots(figsize=(5.8, 3.6))
    lineplot(ax,
             lambda dp, d, c=col, s=scale: W3[c].median()[(dp, d)] * s,
             lambda dp, d, c=col, s=scale: W3[c].std()[(dp, d)] * s)
    ax.set_ylabel(ylab)
    ax.set_title(title)
    ax.legend(frameon=False, loc="upper left")
    ax.axhline(0, color="black", lw=0.6)
    if col == "IR":
        ax.set_yscale("symlog", linthresh=0.1)
        ax.annotate("lower is better", (11, ax.get_ylim()[1] * 0.55),
                    fontsize=8, style="italic", color="#444444")
    else:
        ax.annotate("lower is better", (11, ax.get_ylim()[1] * 0.88),
                    fontsize=8, style="italic", color="#444444")
    save(fig, name)

# ---------------------------------------------------------------- Figure 7
fig, ax = plt.subplots(figsize=(6.0, 3.7))
for dp in DP:
    mean = [W1["ct_utilisation_mean"].mean()[(dp, d)] * 100 for d in DENS]
    peak = [W1["ct_utilisation"].mean()[(dp, d)] * 100 for d in DENS]
    ax.plot(DENS, mean, color=COLOR[dp], marker=MARK[dp], ls=LS[dp],
            lw=1.6, ms=6, markerfacecolor="white", markeredgewidth=1.4,
            label=f"{LABEL[dp]} \u2014 cluster mean")
    ax.plot(DENS, peak, color=COLOR[dp], marker=MARK[dp], ls=LS[dp],
            lw=1.1, ms=4.5, alpha=0.45, label=f"{LABEL[dp]} \u2014 hottest node")
    ax.fill_between(DENS, mean, peak, color=COLOR[dp], alpha=0.10)
ax.axhline(100, color="black", ls=":", lw=1.2)
ax.annotate("map / table capacity", (11, 101.5), fontsize=8)
ax.set_xscale("log"); ax.set_xticks(DENS)
ax.set_xticklabels([str(d) for d in DENS])
ax.set_xlabel("Tenant count (log scale)")
ax.set_ylabel("Connection-tracking table utilisation (%)")
ax.set_title("Conntrack utilisation vs tenant count (W1)")
ax.set_ylim(0, 118)
ax.legend(frameon=False, fontsize=7.4, ncol=1, loc="center left")
ax.annotate("eBPF map saturates on the\nhottest node from ~50 tenants",
            xy=(50, 94.7), xytext=(58, 55), fontsize=7.8, color="#2D6E9E",
            arrowprops=dict(arrowstyle="->", color="#2D6E9E", lw=0.9))
save(fig, "fig7_conntrack_utilisation")

# ---------------------------------------------------------------- Figure 8
fig, ax = plt.subplots(figsize=(6.2, 3.6))
x = np.arange(len(DENS)); w = 0.26
for i, dp in enumerate(DP):
    agent = np.array([W1["agent_rss_mb"].mean()[(dp, d)] for d in DENS])
    state = np.array([W1["state_mb"].mean()[(dp, d)] for d in DENS])
    ax.bar(x + (i - 1) * w, agent, w, color=COLOR[dp], edgecolor="black",
           linewidth=0.5, label=f"{LABEL[dp]} \u2014 agent RSS")
    ax.bar(x + (i - 1) * w, state, w, bottom=agent, color=COLOR[dp],
           edgecolor="black", linewidth=0.5, alpha=0.42, hatch="..",
           label=f"{LABEL[dp]} \u2014 datapath state")
    for xi, tot in zip(x + (i - 1) * w, agent + state):
        ax.annotate(f"{tot:.0f}", (xi, tot), ha="center", va="bottom",
                    fontsize=7, xytext=(0, 1.5), textcoords="offset points")
ax.set_xticks(x); ax.set_xticklabels([str(d) for d in DENS])
ax.set_xlabel("Tenant count"); ax.set_ylabel("Resident memory per node (MB)")
ax.set_title("Datapath memory footprint vs tenant count (W1)")
ax.legend(frameon=False, fontsize=7.2, ncol=2, loc="upper left")
ax.set_ylim(0, 620)
ax.annotate("eBPF pre-allocates its maps:\nflat in density, ~4\u00d7 netfilter at low density",
            xy=(0.26, 438), xytext=(0.55, 505), fontsize=7.6, color="#2D6E9E",
            arrowprops=dict(arrowstyle="->", color="#2D6E9E", lw=0.9))
save(fig, "fig8_memory_footprint")

# ---------------------------------------------------------------- Figure 9
fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.6))
scen = [("W1", 100), ("W1", 200), ("W3", 200), ("W4", 200)]
labels = ["W1\n100 tenants", "W1\n200 tenants", "W3\n200 tenants",
          "W4 (adversarial)\n200 tenants"]
x = np.arange(len(scen)); w = 0.26
for i, dp in enumerate(DP):
    y, e = [], []
    for prof, d in scen:
        s = runs[(runs.profile == prof) & (runs.density == d) & (runs.datapath == dp)]
        y.append(s.conn_fail_pct.mean()); e.append(s.conn_fail_pct.std())
    b = axes[0].bar(x + (i - 1) * w, y, w, label=LABEL[dp], color=COLOR[dp],
                    edgecolor="black", linewidth=0.5, hatch=HATCH[dp],
                    yerr=e, capsize=2.5, error_kw=dict(elinewidth=0.9))
    for rect, v in zip(b, y):
        axes[0].annotate(f"{v:.2f}", (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                         ha="center", va="bottom", fontsize=7, xytext=(0, 2.5),
                         textcoords="offset points")
axes[0].set_xticks(x); axes[0].set_xticklabels(labels, fontsize=8)
axes[0].set_ylabel("Connection-establishment failure rate (%)")
axes[0].set_title("(a) Failure rate by workload profile")
axes[0].legend(frameon=False, fontsize=8, loc="upper left")

fm1 = res["tables"]["fm1_sensitivity"]
ks = ["default_524288", "tuned_2097152"]
names = ["default\nbpf-ct-global-tcp-max\n= 524,288", "tuned\nbpf-ct-global-tcp-max\n= 2,097,152"]
fr = [fm1[k]["conn_fail_pct"] for k in ks]
mem = [fm1[k]["pinned_ct_mb"] for k in ks]
xx = np.arange(2)
b = axes[1].bar(xx - 0.17, fr, 0.34, color="#B23A3A", edgecolor="black",
                linewidth=0.5, label="Failure rate (%)")
for rect, v in zip(b, fr):
    axes[1].annotate(f"{v:.2f}%", (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                     ha="center", va="bottom", fontsize=8, xytext=(0, 2),
                     textcoords="offset points")
axes[1].set_ylabel("Failure rate (%)", color="#B23A3A")
axes[1].tick_params(axis="y", labelcolor="#B23A3A")
axes[1].set_xticks(xx); axes[1].set_xticklabels(names, fontsize=7.8)
axes[1].set_title("(b) FM-1 mitigation: map sizing vs memory (W4, 200 tenants)")
ax2 = axes[1].twinx()
ax2.grid(False)
b2 = ax2.bar(xx + 0.17, mem, 0.34, color="#2D6E9E", edgecolor="black",
             linewidth=0.5, alpha=0.75, label="Pinned CT map (MB)")
for rect, v in zip(b2, mem):
    ax2.annotate(f"{v:.0f} MB", (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                 ha="center", va="bottom", fontsize=8, xytext=(0, 2),
                 textcoords="offset points")
ax2.set_ylabel("Pinned conntrack map memory (MB)", color="#2D6E9E")
ax2.tick_params(axis="y", labelcolor="#2D6E9E")
ax2.set_ylim(0, max(mem) * 1.35)
axes[1].set_ylim(0, max(fr) * 1.35)
save(fig, "fig9_failure_rate")

# --------------------------------------------------- Figure 10: validation
val = res["validation"]
fig, axes = plt.subplots(2, 2, figsize=(9.4, 7.0))

# (a) FD reproduces Jain's index on known allocations
ax = axes[0, 0]
frac = np.linspace(0.02, 1.0, 60)
n = 100
fd_curve, jain_curve = [], []
for f in frac:
    k = max(1, int(round(f * n)))
    xv = np.concatenate([np.ones(k), np.zeros(n - k)])
    j = (xv.sum() ** 2) / (n * (xv ** 2).sum())
    jain_curve.append(j)
    fd_curve.append(1 - j)
ax.plot(frac, jain_curve, color="#2D6E9E", lw=1.8, label="Jain's fairness index $J$")
ax.plot(frac, fd_curve, color="#B23A3A", lw=1.8, ls="--", label="FD $= 1 - J$")
ax.plot(frac, [1 - f for f in frac], color="black", lw=0.9, ls=":",
        label="analytic $1 - k/n$")
b = val["fd_boundary_checks"]
ax.scatter([1.0, 0.5, 0.01], [b["perfect_equality"], b["half_starved"],
                              b["single_winner"]],
           color="#B23A3A", zorder=5, s=42, marker="D", edgecolor="black",
           linewidth=0.6, label="measured boundary cases")
ax.set_xlabel("Fraction of tenants receiving their requested share ($k/n$)")
ax.set_ylabel("Index value")
ax.set_title("(a) FD is an exact affine transform of Jain's index")
ax.legend(frameon=False, fontsize=7.6, loc="center right")

# (b) pairwise Spearman correlation heatmap
ax = axes[0, 1]
mets = ["IR", "FD", "BR"]
M = np.eye(3)
pc = val["pairwise_correlation"]
for i, a in enumerate(mets):
    for j, bb in enumerate(mets):
        if i == j:
            continue
        k = f"{a}_vs_{bb}" if f"{a}_vs_{bb}" in pc else f"{bb}_vs_{a}"
        M[i, j] = pc[k]["rho"]
im = ax.imshow(M, cmap="RdYlBu_r", vmin=0, vmax=1)
ax.set_xticks(range(3)); ax.set_xticklabels(mets)
ax.set_yticks(range(3)); ax.set_yticklabels(mets)
ax.grid(False)
for i in range(3):
    for j in range(3):
        ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center",
                fontsize=11, color="black" if M[i, j] < 0.8 else "white")
ax.set_title("(b) Spearman $\\rho$ between isolation metrics")
ev = val["pca_explained_variance_ratio"]
ax.set_xlabel(f"PCA: PC1={ev[0]*100:.0f}%, PC2={ev[1]*100:.0f}%, PC3={ev[2]*100:.0f}%\n"
              f"{val['pca_components_for_95pct']} components needed for 95% variance",
              fontsize=8)
plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

# (c) BR threshold sensitivity
ax = axes[1, 0]
sens = val["br_threshold_sensitivity"]
thr = [s["threshold"] * 100 for s in sens]
for dp in DP:
    ax.plot(thr, [s["d200"][dp] * 100 for s in sens], color=COLOR[dp],
            marker=MARK[dp], ls=LS[dp], lw=1.6, ms=5.5,
            markerfacecolor="white", markeredgewidth=1.3,
            label=f"{LABEL[dp]} (200 tenants)")
    ax.plot(thr, [s["d100"][dp] * 100 for s in sens], color=COLOR[dp],
            marker=MARK[dp], ls=LS[dp], lw=1.0, ms=4, alpha=0.45,
            label=f"{LABEL[dp]} (100 tenants)")
ax.axvline(20, color="black", ls=":", lw=1.2)
ax.annotate("threshold used\nin the paper", (20.8, 8), fontsize=7.6)
ax.set_xlabel("Degradation threshold defining \u201caffected\u201d (%)")
ax.set_ylabel("Blast Radius (%)")
ax.set_title("(c) BR ordering is robust to the threshold choice")
ax.legend(frameon=False, fontsize=7.0, ncol=1, loc="upper right")

# (d) construct validity: IR vs node utilisation
ax = axes[1, 1]
t3 = tenants[(tenants.profile == "W3") & (tenants.role == "victim")
             & (tenants.phase == "agg")]
rho = t3.groupby(["datapath", "density", "run"]).rho.mean().rename("rho_agg")
w3r = runs[runs.profile == "W3"].set_index(["datapath", "density", "run"]).join(rho)
for dp in DP:
    s = w3r.loc[dp]
    ax.scatter(s.rho_agg, s.IR, s=26, color=COLOR[dp], marker=MARK[dp],
               alpha=0.65, edgecolor="black", linewidth=0.4, label=LABEL[dp])
ax.set_yscale("symlog", linthresh=0.1)
ax.set_xlabel("Mean node softirq utilisation $\\rho$ during aggression")
ax.set_ylabel("Interference Ratio")
r = val["IR_vs_utilisation"]["rho"]
ax.set_title(f"(d) IR tracks queueing utilisation (Spearman $\\rho$={r:.2f})")
ax.axvline(0.90, color="black", ls=":", lw=1.1)
ax.annotate("queueing knee", (0.915, ax.get_ylim()[0] * 0.6), fontsize=7.6, rotation=90)
ax.legend(frameon=False, fontsize=8, loc="upper left")

fig.suptitle("Construct validation of the isolation metrics", y=1.00, fontsize=12)
fig.tight_layout()
save(fig, "fig10_metric_validation")

print("\nAll figures written to", FIG)
