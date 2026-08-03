#!/usr/bin/env python3
"""MTDP-Bench node-local state harvester.

Runs as a privileged DaemonSet on every worker. Once per interval it samples
the *kernel* state that each datapath actually maintains, plus the CPU cost of
maintaining it. This is the component that produces the paper's state-footprint
and conntrack-utilisation numbers.

Design constraint (paper principle P1, datapath neutrality): the harvester must
not use the datapath's own observability plane. We therefore read /proc, /sys
and the standard CLI tools, and we do NOT enable Hubble. Cilium's own metrics
endpoint is scraped only for agent RSS, never for traffic accounting.

Everything is best-effort per source: on a DP-IPT node there is no bpftool map
to read, and that is expected, not an error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

NODE = os.environ.get("MTDP_NODE", os.uname().nodename)
PROC = os.environ.get("MTDP_PROC", "/host/proc")
SYS = os.environ.get("MTDP_SYS", "/host/sys")


def _run(cmd, timeout=15):
    """Run a command, returning stdout or None. Never raises."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return p.stdout if p.returncode == 0 else None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def conntrack_state() -> dict:
    """netfilter conntrack occupancy. Applies to DP-IPT and DP-IPVS.

    nf_conntrack_count is the single most important number in the whole study:
    it is what saturates, and saturation is what produces the tail-latency and
    connection-failure cliffs.
    """
    count = _read(f"{PROC}/sys/net/netfilter/nf_conntrack_count")
    maximum = _read(f"{PROC}/sys/net/netfilter/nf_conntrack_max")
    buckets = _read(f"{PROC}/sys/net/netfilter/nf_conntrack_buckets")
    out = {
        "nf_count": int(count) if count else None,
        "nf_max": int(maximum) if maximum else None,
        "nf_buckets": int(buckets) if buckets else None,
    }
    if out["nf_count"] is not None and out["nf_max"]:
        out["nf_utilisation"] = out["nf_count"] / out["nf_max"]

    # Insert failures / drops are the netfilter analogue of a BPF LRU eviction.
    stat = _read(f"{PROC}/net/stat/nf_conntrack")
    if stat:
        lines = stat.splitlines()
        if len(lines) > 1:
            cols = lines[0].split()
            totals = {c: 0 for c in cols}
            for ln in lines[1:]:
                for c, v in zip(cols, ln.split()):
                    try:
                        totals[c] += int(v, 16)
                    except ValueError:
                        pass
            for k in ("insert_failed", "drop", "early_drop", "invalid",
                      "search_restart"):
                if k in totals:
                    out[f"nf_{k}"] = totals[k]
    return out


def bpf_state() -> dict:
    """Cilium BPF map occupancy. Applies to DP-EBPF only.

    We read the map sizes via bpftool rather than via `cilium-dbg bpf ct list`
    because the latter walks every entry and, at ~500k entries under W4, that
    walk is itself a measurable perturbation of the thing being measured.
    """
    out = {}
    raw = _run(["bpftool", "--json", "map", "show"])
    if not raw:
        return out
    try:
        maps = json.loads(raw)
    except json.JSONDecodeError:
        return out

    total_bytes = 0
    for m in maps:
        name = m.get("name") or ""
        mx = m.get("max_entries", 0)
        # bytes_memlock is the pinned allocation, which is what shows up in the
        # paper's "state memory" column for the eBPF arm.
        total_bytes += m.get("bytes_memlock", 0)
        if name.startswith("cilium_ct4_global") or name.startswith("cilium_ct_any4"):
            out.setdefault("ct_maps", []).append({"name": name, "max_entries": mx})
        if name.startswith("cilium_lb4_services"):
            out["lb4_services_max"] = mx
    out["bpf_memlock_bytes"] = total_bytes

    # Live occupancy: cilium-dbg exposes per-map counts cheaply.
    raw = _run(["cilium-dbg", "bpf", "ct", "count"]) or _run(["cilium", "bpf", "ct", "count"])
    if raw:
        m = re.search(r"(\d+)", raw)
        if m:
            out["bpf_ct_count"] = int(m.group(1))
    if out.get("bpf_ct_count") is not None and out.get("ct_maps"):
        cap = sum(x["max_entries"] for x in out["ct_maps"])
        if cap:
            out["bpf_ct_capacity"] = cap
            out["bpf_ct_utilisation"] = out["bpf_ct_count"] / cap
    return out


def service_programming_state() -> dict:
    """How many rules/entries the datapath is holding to express Services.

    This is the 'service entries' column: iptables rules for DP-IPT, virtual
    servers for DP-IPVS, BPF map entries for DP-EBPF. They are not the same
    unit, which is exactly the point the paper makes -- so we record the unit
    alongside the count instead of pretending they are comparable scalars.
    """
    out = {}
    raw = _run(["iptables-save", "-t", "nat"])
    if raw is not None:
        rules = [l for l in raw.splitlines() if l.startswith("-A")]
        out["iptables_nat_rules"] = len(rules)
        out["iptables_kube_svc_chains"] = sum(
            1 for l in raw.splitlines() if l.startswith(":KUBE-SVC-"))
        out["entry_kind"] = "iptables_rules"

    raw = _run(["ipvsadm", "-Ln"])
    if raw is not None:
        out["ipvs_virtual_servers"] = sum(
            1 for l in raw.splitlines() if l.startswith("TCP") or l.startswith("UDP"))
        out["ipvs_real_servers"] = sum(
            1 for l in raw.splitlines() if l.strip().startswith("->"))
        out["entry_kind"] = "ipvs_virtual_servers"

    if "lb4_services_max" in bpf_state():
        out["entry_kind"] = "bpf_map_entries"
    return out


def softirq_cpu() -> dict:
    """Datapath CPU attribution.

    Honest limitation (paper L3): softirq time is the closest node-local proxy
    we have for datapath cost without hardware counters. It over-counts, because
    it includes NIC-driver work common to all three arms. We report the raw
    counters and let the analyser difference them against the idle baseline.
    """
    out = {}
    stat = _read(f"{PROC}/stat")
    if stat:
        for ln in stat.splitlines():
            if ln.startswith("cpu "):
                f = [int(x) for x in ln.split()[1:]]
                names = ["user", "nice", "system", "idle", "iowait",
                         "irq", "softirq", "steal"]
                out.update({f"cpu_{n}": v for n, v in zip(names, f)})
                out["cpu_total"] = sum(f)
                break
    si = _read(f"{PROC}/softirqs")
    if si:
        for ln in si.splitlines():
            if ln.strip().startswith(("NET_RX", "NET_TX")):
                k = ln.split(":")[0].strip()
                out[f"softirq_{k}"] = sum(int(x) for x in ln.split(":")[1].split())
    out["online_cpus"] = os.cpu_count()
    return out


def agent_rss() -> dict:
    """Resident memory of the datapath control agent (cilium-agent/kube-proxy)."""
    out = {}
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        comm = _read(f"{PROC}/{pid}/comm")
        if comm not in ("cilium-agent", "kube-proxy", "calico-node"):
            continue
        status = _read(f"{PROC}/{pid}/status") or ""
        m = re.search(r"VmRSS:\s+(\d+) kB", status)
        if m:
            out[f"{comm}_rss_mb"] = round(int(m.group(1)) / 1024.0, 2)
    return out


def sample() -> dict:
    rec = {
        "ts": time.time(),
        "node": NODE,
        "datapath": os.environ.get("MTDP_DATAPATH", "unknown"),
        "campaign": os.environ.get("MTDP_CAMPAIGN", ""),
    }
    rec.update(conntrack_state())
    rec.update(bpf_state())
    rec.update(service_programming_state())
    rec.update(softirq_cpu())
    rec.update(agent_rss())
    return rec


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--interval-s", type=float, default=5.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--out", default="-")
    a = p.parse_args(argv)

    sink = sys.stdout if a.out == "-" else open(a.out, "a")
    try:
        while True:
            sink.write(json.dumps(sample()) + "\n")
            sink.flush()
            if a.once:
                return 0
            time.sleep(a.interval_s)
    except KeyboardInterrupt:
        return 0
    finally:
        if sink is not sys.stdout:
            sink.close()


if __name__ == "__main__":
    sys.exit(main())
