#!/usr/bin/env python3
"""Measure rule_program_ms: API-accept -> datapath-programmed, per Service.

This is the W2 probe and the single most delicate measurement in the study.

Definition used throughout the paper:
  t0 = creationTimestamp of the EndpointSlice becoming ready (control-plane accept)
  t1 = first instant at which the backend is resolvable in the NODE-LOCAL
       datapath state (iptables NAT chain / IPVS destination / eBPF lb4 map)

We poll the node-local state rather than reading a controller's self-reported
latency metric, because each datapath's own metric measures a different thing
(kube-proxy's sync_proxy_rules_duration is a whole-table resync, not a
per-Service latency). Polling gives one comparable definition across all three
arms. The cost is quantisation at the poll interval, which we set to 2 ms and
report as the measurement floor.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

POLL_INTERVAL_S = 0.002   # 2 ms measurement floor; reported in the paper
DEFAULT_TIMEOUT_S = 30.0


def _run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def programmed_iptables(clusterip, port):
    out = _run(["iptables-save", "-t", "nat"])
    needle = "%s/32" % clusterip
    return needle in out and ("dport %s" % port) in out


def programmed_ipvs(clusterip, port):
    out = _run(["ipvsadm", "-Ln"])
    header = "%s:%s" % (clusterip, port)
    if header not in out:
        return False
    # A vserver with zero real servers is NOT programmed; traffic would blackhole.
    tail = out.split(header, 1)[1]
    for line in tail.splitlines()[1:]:
        if line.startswith("TCP") or line.startswith("UDP"):
            break
        if "->" in line:
            return True
    return False


def programmed_ebpf(clusterip, port):
    out = _run(["cilium-dbg", "service", "list", "-o", "json"])
    if not out:
        return False
    try:
        services = json.loads(out)
    except json.JSONDecodeError:
        return False
    target = "%s:%s" % (clusterip, port)
    for svc in services:
        fe = svc.get("frontend-address", {})
        if "%s:%s" % (fe.get("ip"), fe.get("port")) == target:
            return bool(svc.get("backend-addresses"))
    return False


PROBES = {
    "DP-IPT": programmed_iptables,
    "DP-IPVS": programmed_ipvs,
    "DP-EBPF": programmed_ebpf,
}


def measure(datapath, clusterip, port, t0, timeout_s):
    """Poll until programmed; return elapsed ms, or None on timeout."""
    probe = PROBES[datapath]
    deadline = t0 + timeout_s
    while time.time() < deadline:
        if probe(clusterip, port):
            return (time.time() - t0) * 1000.0
        time.sleep(POLL_INTERVAL_S)
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Probe per-Service programming latency")
    p.add_argument("--datapath", required=True, choices=sorted(PROBES))
    p.add_argument("--clusterip", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--t0", type=float, default=None,
                   help="epoch seconds of control-plane accept; default = now")
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--tenant", default=os.environ.get("MTDP_TENANT", ""))
    a = p.parse_args(argv)

    t0 = a.t0 if a.t0 is not None else time.time()
    ms = measure(a.datapath, a.clusterip, a.port, t0, a.timeout_s)
    rec = {
        "tenant": a.tenant,
        "datapath": a.datapath,
        "clusterip": a.clusterip,
        "port": a.port,
        "rule_program_ms": ms,
        "timed_out": ms is None,
        "poll_floor_ms": POLL_INTERVAL_S * 1000.0,
        "observed_at": t0,
    }
    print(json.dumps(rec))
    return 0 if ms is not None else 1


if __name__ == "__main__":
    sys.exit(main())
