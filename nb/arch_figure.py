#!/usr/bin/env python3
"""MTDP-Bench harness architecture diagram (Figure 1).

Publication-quality block diagram, grayscale-safe, 200 dpi.
Pure matplotlib primitives, no data dependencies.

    python3 nb/arch_figure.py --out figs/fig1_architecture.png
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

_ap = argparse.ArgumentParser(description="Render the MTDP-Bench architecture figure")
_ap.add_argument("--out", default="figs/fig1_architecture.png")
_args = _ap.parse_args()
os.makedirs(os.path.dirname(os.path.abspath(_args.out)), exist_ok=True)

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "DejaVu Serif"],
    "font.size": 8.5,
})

C_H, C_HE = "#e8eef6", "#2c4f7c"        # harness
C_C, C_CE = "#f3f0e7", "#7a6a45"        # cluster / SUT
C_A, C_AE = "#eaf1ea", "#3d6b45"        # analysis

FIG_W, FIG_H = 10.0, 6.4
XMAX, YMAX = 104.0, 64.0
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, XMAX)
ax.set_ylim(0, YMAX)
ax.axis("off")

UPI = XMAX / FIG_W                      # data units per inch


def fits(text, fontsize, width):
    """Assert the widest line of `text` fits inside `width` data units."""
    upc = 0.5 * fontsize / 72.0 * UPI
    longest = max(len(ln) for ln in text.split("\n"))
    need = longest * upc
    if need > width:
        raise AssertionError(
            f"text overflows: {need:.1f}u needed > {width:.1f}u available "
            f"@fs{fontsize} -> {text.splitlines()[0][:60]!r}")
    return True


def box(x, y, w, h, label, sub=None, fc=C_H, ec=C_HE, lw=1.1,
        fs=8.5, subfs=None, bold=True, radius=1.1, zorder=3):
    subfs = subfs if subfs is not None else fs - 1.7
    fits(label, fs, w - 1.4)
    if sub:
        fits(sub, subfs, w - 1.4)
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zorder))
    if sub:
        ax.text(x + w / 2, y + h - 1.55, label, ha="center", va="center",
                fontsize=fs, fontweight="bold" if bold else "normal",
                color="#111111", zorder=zorder + 1)
        ax.text(x + w / 2, y + (h - 3.1) / 2, sub, ha="center", va="center",
                fontsize=subfs, color="#444444", zorder=zorder + 1,
                linespacing=1.35)
    else:
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=fs, fontweight="bold" if bold else "normal",
                color="#111111", zorder=zorder + 1, linespacing=1.35)


def panel(x, y, w, h, fc, ec):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=1.4",
        facecolor=fc, edgecolor=ec, linewidth=0.9,
        linestyle=(0, (4, 2)), zorder=1))


def arrow(p0, p1, color=C_HE, lw=1.3, ls="-", rad=0.0, zorder=6, mut=11):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle="-|>", mutation_scale=mut,
        connectionstyle=f"arc3,rad={rad}", color=color, linewidth=lw,
        linestyle=ls, zorder=zorder, shrinkA=1.5, shrinkB=1.5))


def alabel(x, y, txt, fs=6.6, color=C_HE, ha="center"):
    ax.text(x, y, txt, ha=ha, va="center", fontsize=fs, color=color,
            style="italic", zorder=8, linespacing=1.25,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none",
                      alpha=0.93))


# ============================================ column titles (non-overlapping)
ax.text(1.4, 61.6, "MTDP-Bench Harness  (experiment control)",
        fontsize=8.0, fontweight="bold", color=C_HE, ha="left")
ax.text(30.2, 61.6, "System Under Test  \u2014  Kubernetes cluster",
        fontsize=8.0, fontweight="bold", color=C_CE, ha="left")
ax.text(74.5, 61.6, "Measurement & Analysis",
        fontsize=8.0, fontweight="bold", color=C_AE, ha="left")

# ============================================ 1. HARNESS COLUMN
panel(1.2, 39.4, 24.6, 20.6, "#f7f9fc", C_HE)

box(2.6, 53.4, 21.8, 5.6, "Declarative Campaign Config",
    "config/tenant_mix.yaml + image digest",
    fc="#ffffff", ec=C_HE, fs=8.0, subfs=6.6)

box(2.6, 46.6, 10.3, 5.8, "Provisioner", "prov/\nrenders N tenants",
    fc=C_H, ec=C_HE, fs=8.0, subfs=6.5)
box(14.1, 46.6, 10.3, 5.8, "Orchestrator", "orch/\nNTP phase sync",
    fc=C_H, ec=C_HE, fs=8.0, subfs=6.5)

box(2.6, 40.6, 21.8, 5.0, "Traffic Generators",
    "wrk2  \u2502  iperf3  \u2502  churngen",
    fc="#dde7f3", ec=C_HE, fs=8.0, subfs=7.0)

arrow((13.5, 53.4), (7.75, 52.4))
arrow((13.5, 53.4), (19.25, 52.4))
arrow((7.75, 46.6), (7.75, 45.6))
arrow((19.25, 46.6), (19.25, 45.6))

# ============================================ 2. SYSTEM UNDER TEST
panel(29.6, 8.6, 40.8, 51.4, "#fdfcf8", C_CE)

ax.add_patch(FancyBboxPatch(
    (30.9, 49.6), 38.2, 9.4, boxstyle="round,pad=0,rounding_size=1.1",
    facecolor=C_C, edgecolor=C_CE, linewidth=1.1, zorder=3))
ax.text(50.0, 57.4, "Tenant Population   (10 / 50 / 100 / 200 namespaces)",
        ha="center", va="center", fontsize=8.2, fontweight="bold",
        color="#111111", zorder=4)
for x, name, share, fc in [
        (31.9, "T-Web", "50%", "#ffffff"),
        (41.2, "T-Churn", "20%", "#ffffff"),
        (50.5, "T-Bulk", "20%  aggressor", "#f6e3e3"),
        (59.8, "T-Latency", "10%  victim", "#e3edf6")]:
    box(x, 50.3, 8.3, 5.0, name, share, fc=fc, ec=C_CE, lw=0.9,
        fs=7.6, subfs=6.4, radius=0.8, zorder=5)

ax.text(50.0, 48.1,
        "each tenant: ResourceQuota \u2502 LimitRange \u2502 default-deny\n"
        "NetworkPolicy \u2502 ClusterIP Service \u2502 3 backend replicas",
        ha="center", va="center", fontsize=6.4, color="#5a5241",
        style="italic", linespacing=1.3)

box(30.9, 41.0, 38.2, 5.2, "Kubernetes Control Plane",
    "API server \u2192 EndpointSlice watch \u2192 datapath programming",
    fc="#ffffff", ec=C_CE, fs=8.2, subfs=6.8)

# --- independent variable
ax.add_patch(FancyBboxPatch(
    (30.9, 23.8), 38.2, 14.6,
    boxstyle="round,pad=0,rounding_size=1.2",
    facecolor="#f2eee2", edgecolor="#8a7a52", linewidth=1.4, zorder=2))
ax.text(50.0, 36.8, "Forwarding Datapath \u2014 controlled independent variable",
        ha="center", va="center", fontsize=8.2, fontweight="bold",
        color="#5a4d2a", zorder=4)

box(31.9, 25.0, 11.7, 10.2, "DP-IPT",
    "kube-proxy\niptables mode\n\nO(n) NAT chain\nkernel conntrack",
    fc="#ffffff", ec="#333333", fs=8.2, subfs=6.6, radius=0.9)
box(44.1, 25.0, 11.7, 10.2, "DP-IPVS",
    "kube-proxy\nipvs mode (rr)\n\nhash vserver LB\nkernel conntrack",
    fc="#ffffff", ec="#333333", fs=8.2, subfs=6.6, radius=0.9)
box(56.3, 25.0, 11.7, 10.2, "DP-EBPF",
    "Cilium 1.16\nkube-proxy replaced\n\nTC / XDP programs\nfixed-size BPF CT map",
    fc="#eef3f8", ec="#333333", fs=8.2, subfs=6.6, radius=0.9, lw=1.4)

ax.text(50.0, 22.0,
        "exactly one datapath active per run \u2014 all other node settings\n"
        "asserted identical by a preflight check that aborts on drift",
        ha="center", va="center", fontsize=6.4, color="#5a5241",
        style="italic", linespacing=1.3)

box(30.9, 14.6, 38.2, 5.4, "Linux Kernel 6.8 \u2014 shared node resources",
    "softirq CPU budget \u2502 conntrack / BPF map memory \u2502 NIC queues",
    fc="#ecebe6", ec="#6b6b6b", fs=8.2, subfs=6.6)
box(30.9, 9.6, 38.2, 4.2,
    "13 worker nodes: 2\u00d7 Xeon Silver 4314 (32c) \u2502 128 GB DDR4\n"
    "ConnectX-5 25 GbE \u2502 single non-blocking leaf switch",
    fc="#e2e0da", ec="#6b6b6b", fs=6.8, bold=False)

# ============================================ 3. MEASUREMENT & ANALYSIS
panel(74.0, 8.6, 24.8, 51.4, "#f6faf6", C_AE)

box(75.3, 49.6, 22.2, 9.4, "Load-Generator Telemetry",
    "per-tenant p50 / p99 latency\nachieved vs requested goodput\n"
    "connection-establishment failures",
    fc=C_A, ec=C_AE, fs=8.2, subfs=6.6)

box(75.3, 36.6, 22.2, 11.0, "State Harvester  (harv/)",
    "DaemonSet, 5 s epoch\n\niptables-save \u2502 ipvsadm \u2502 bpftool\n"
    "conntrack -C \u2502 cgroup v2 cpu.stat\n\u2192 one normalized schema",
    fc=C_A, ec=C_AE, fs=8.2, subfs=6.6)

box(75.3, 30.4, 22.2, 4.6, "Time-Series Store  (InfluxDB)",
    fc="#ffffff", ec=C_AE, fs=8.2)

box(75.3, 17.0, 22.2, 11.8, "Analyzer  (nb/)",
    "warm-up trim \u2192 10-rep aggregation\n"
    "Mann\u2013Whitney U + Holm\u2013Bonferroni\nCliff's \u03b4 effect size\n\n"
    "isolation.py \u2192 IR / FD / BR\nvalidate_metrics.py \u2192 \u00a73.5",
    fc=C_A, ec=C_AE, fs=8.2, subfs=6.6)

box(75.3, 9.6, 22.2, 5.8, "Released Artifact",
    "raw CSV \u2502 notebooks \u2502 manifests\ngithub.com/AboalfazlForooghi2004/mtdp-bench",
    fc="#ffffff", ec=C_AE, fs=8.2, subfs=5.6)

# ============================================ inter-stage arrows
arrow((24.4, 48.4), (30.9, 54.3), rad=0.10, lw=1.5)
alabel(28.0, 52.6, "apply\ntenants")

arrow((24.4, 43.1), (30.9, 31.0), rad=-0.10, lw=1.5)
alabel(27.4, 38.4, "offered\nload")

arrow((50.0, 49.6), (50.0, 46.2), lw=1.2, color=C_CE)
arrow((50.0, 41.0), (50.0, 38.4), lw=1.2, color=C_CE)
alabel(61.6, 39.6, "rule_program_ms probe (W2)", fs=6.4, color=C_CE)
arrow((50.0, 23.8), (50.0, 20.0), lw=1.2, color=C_CE)
arrow((50.0, 14.6), (50.0, 13.8), lw=1.2, color=C_CE)

arrow((69.1, 31.0), (75.3, 42.0), rad=0.12, lw=1.5, color=C_AE)
alabel(72.0, 36.4, "native\ninventory", color=C_AE)

# analysis chain
for y0, y1 in [(49.6, 47.6), (36.6, 35.0), (30.4, 28.8), (17.0, 15.4)]:
    arrow((86.4, y0), (86.4, y1), lw=1.3, color=C_AE)

# ============================================ P1 datapath-neutral path
P1 = dict(color=C_HE, lw=1.2, ls=(0, (5, 2.5)), zorder=2)
ax.plot([13.5, 13.5], [40.6, 4.4], **P1)
ax.plot([13.5, 101.6], [4.4, 4.4], **P1)
ax.plot([101.6, 101.6], [4.4, 54.3], **P1)
arrow((101.6, 54.3), (97.5, 54.3), lw=1.2, ls=(0, (5, 2.5)), color=C_HE)
alabel(53.0, 4.4,
       "P1 \u2014 datapath neutrality: every primary metric is collected OUTSIDE the "
       "datapath (load generators, /proc, cgroup v2),\nnever from datapath-native "
       "telemetry such as Hubble, which has no netfilter equivalent",
       fs=6.6)

# ============================================ legend
ax.legend(handles=[
    Line2D([0], [0], color=C_HE, lw=1.4, label="experiment control / stimulus"),
    Line2D([0], [0], color=C_CE, lw=1.4, label="in-cluster forwarding path"),
    Line2D([0], [0], color=C_AE, lw=1.4, label="measurement / analysis"),
    Line2D([0], [0], color=C_HE, lw=1.2, ls=(0, (5, 2.5)),
           label="datapath-neutral measurement path (P1)"),
], loc="upper left", bbox_to_anchor=(0.0, 0.035), frameon=False,
    fontsize=6.9, ncol=4, handlelength=2.4, columnspacing=2.0)

fig.tight_layout(pad=0.3)
fig.savefig(_args.out, dpi=200, bbox_inches="tight", facecolor="white")
print("OK:", _args.out)
