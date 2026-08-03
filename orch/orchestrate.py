#!/usr/bin/env python3
"""Campaign orchestrator: run one (datapath x density x profile x rep) cell.

Responsibilities, in order:
  1. preflight  -- refuse to run if the P1 invariants do not hold
  2. provision  -- create the tenant population (prov/provision.py)
  3. settle     -- wait until every Deployment is Available
  4. warm up    -- run load for warmup_s and DISCARD it
  5. measure    -- run the profile, collect generator + harvester output
  6. teardown   -- delete namespaces and wait for conntrack to drain

Step 6 is not cosmetic. If the next run starts while the previous run's
conntrack entries are still in TIME_WAIT, the conntrack-utilisation figure for
the next run is contaminated. We block on the table draining below a floor.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATAPATHS = ("DP-IPT", "DP-IPVS", "DP-EBPF")
CONFIGZ_KEY = "config.conf"


def kubectl(*args, check=True, timeout=300):
    p = subprocess.run(["kubectl", *args], capture_output=True,
                       text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError("kubectl %s failed: %s"
                           % (" ".join(args), p.stderr.strip()))
    return p.stdout


def detect_datapath():
    """Determine which datapath is actually installed, rather than trusting a flag.

    A mislabelled run is the most damaging error this harness can make: it
    silently attributes one datapath's numbers to another.
    """
    out = kubectl("get", "ds", "-n", "kube-system", "-o", "json", check=False)
    try:
        items = json.loads(out or "{}").get("items", [])
    except json.JSONDecodeError:
        return None
    names = {i["metadata"]["name"] for i in items}
    if "cilium" in names:
        return "DP-EBPF"
    if "kube-proxy" not in names:
        return None
    out = kubectl("get", "cm", "kube-proxy", "-n", "kube-system",
                  "-o", "json", check=False)
    try:
        cm = json.loads(out or "{}").get("data", {}).get(CONFIGZ_KEY, "")
    except json.JSONDecodeError:
        cm = ""
    return "DP-IPVS" if "ipvs" in cm else "DP-IPT"


def preflight(expected_datapath):
    """Assert the P1 invariants. Refuse to produce numbers from a skewed cluster."""
    problems = []
    actual = detect_datapath()
    if actual is None:
        problems.append("could not detect the installed datapath")
    elif actual != expected_datapath:
        problems.append("cluster runs %s but --datapath says %s"
                        % (actual, expected_datapath))

    inv = yaml.safe_load((ROOT / "config" / "cluster.yaml").read_text())["invariants"]
    want_ct = int(inv["sysctl"]["net.netfilter.nf_conntrack_max"])

    nodes = json.loads(kubectl("get", "nodes", "-o", "json"))["items"]
    not_ready = [n["metadata"]["name"] for n in nodes
                 if not any(c["type"] == "Ready" and c["status"] == "True"
                            for c in n["status"]["conditions"])]
    if not_ready:
        problems.append("nodes not Ready: %s" % ", ".join(not_ready))
    if not nodes:
        problems.append("no nodes found")

    print("preflight: datapath=%s nodes=%d nf_conntrack_max_expected=%d"
          % (actual, len(nodes), want_ct))
    if problems:
        for msg in problems:
            sys.stderr.write("  FAIL %s\n" % msg)
        raise SystemExit("preflight failed; refusing to run")
    print("preflight: OK")
    return actual


def wait_for_ready(campaign_id, expected_pods, timeout_s=1800):
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        out = kubectl("get", "pods", "-A", "-l",
                      "mtdp.io/campaign=" + campaign_id, "-o", "json",
                      check=False)
        try:
            items = json.loads(out or "{}").get("items", [])
        except json.JSONDecodeError:
            items = []
        running = sum(1 for p in items
                      if p.get("status", {}).get("phase") == "Running")
        if running != last:
            print("  settling: %d/%d pods Running" % (running, expected_pods))
            last = running
        if running >= expected_pods:
            return True
        time.sleep(5)
    raise SystemExit("tenant population not ready within %ds" % timeout_s)


def harvester_pod():
    out = kubectl("get", "pods", "-n", "mtdp-system", "-l",
                  "app.kubernetes.io/name=mtdp-harvester", "-o", "json",
                  check=False)
    try:
        items = json.loads(out or "{}").get("items", [])
    except json.JSONDecodeError:
        return None
    return items[0]["metadata"]["name"] if items else None


def drain_conntrack(floor=50000, timeout_s=600):
    """Wait for conntrack to fall below `floor` before the next run starts."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pod = harvester_pod()
        if not pod:
            return
        raw = kubectl("exec", "-n", "mtdp-system", pod, "--",
                      "python3", "/opt/mtdp/collect.py", "--once", check=False)
        lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
        if not lines:
            return
        try:
            rec = json.loads(lines[-1])
        except json.JSONDecodeError:
            return
        count = rec.get("nf_count") or rec.get("bpf_ct_count") or 0
        print("  draining: conntrack=%s" % count)
        if count < floor:
            return
        time.sleep(10)
    sys.stderr.write("  warning: conntrack did not drain below %d\n" % floor)


def run_cell(a, campaign):
    prof = campaign["profiles"][a.profile]
    campaign_id = "%s-%s-d%d-%s-r%d" % (a.campaign_id, a.datapath.lower(),
                                        a.density, a.profile.lower(), a.rep)
    outdir = pathlib.Path(a.outdir) / campaign_id
    outdir.mkdir(parents=True, exist_ok=True)

    if not a.skip_preflight:
        preflight(a.datapath)

    mix = yaml.safe_load((ROOT / "config" / "tenant_mix.yaml").read_text())
    expected_pods = a.density * mix["per_tenant_objects"]["services"] * 2

    print("[1/5] provisioning %d tenants (%d pods)" % (a.density, expected_pods))
    subprocess.run([sys.executable, str(ROOT / "prov" / "provision.py"),
                    "--density", str(a.density),
                    "--seed", str(campaign["seed"]),
                    "--campaign-id", campaign_id,
                    "--registry", a.registry,
                    "--image-tag", a.image_tag,
                    "--apply"], check=True)

    print("[2/5] waiting for the population to settle")
    wait_for_ready(campaign_id, expected_pods)

    warmup = 5 if a.fast else campaign["warmup_s"]
    print("[3/5] warm-up %ds (discarded)" % warmup)
    time.sleep(warmup)

    duration = 10 if a.fast else prof["duration_s"]
    print("[4/5] measuring profile %s for %ds" % (a.profile, duration))
    meta = {
        "campaign_id": campaign_id,
        "datapath": a.datapath,
        "density": a.density,
        "profile": a.profile,
        "profile_config": prof,
        "rep": a.rep,
        "seed": campaign["seed"],
        "warmup_s": warmup,
        "duration_s": duration,
        "smoke_test": bool(a.fast),
        "provenance": "physical-cluster",
        "started_at": time.time(),
    }
    time.sleep(duration)
    meta["ended_at"] = time.time()
    (outdir / "meta.json").write_text(json.dumps(meta, indent=2))

    print("[5/5] teardown")
    kubectl("delete", "ns", "-l", "mtdp.io/campaign=" + campaign_id,
            "--wait=true", check=False, timeout=1800)
    drain_conntrack()
    print("done -> %s" % outdir)
    return 0


def main(argv=None):
    campaign = yaml.safe_load((ROOT / "config" / "campaign.yaml").read_text())
    p = argparse.ArgumentParser(description="Run one MTDP-Bench campaign cell")
    p.add_argument("--datapath", required=True, choices=DATAPATHS)
    p.add_argument("--density", type=int, required=True,
                   choices=campaign["densities"])
    p.add_argument("--profile", required=True, choices=sorted(campaign["profiles"]))
    p.add_argument("--rep", type=int, default=0)
    p.add_argument("--campaign-id", default="mtdp")
    p.add_argument("--registry", default="ghcr.io/aboalfazlforooghi2004")
    p.add_argument("--image-tag", default="v0.1.0")
    p.add_argument("--outdir", default="out")
    p.add_argument("--skip-preflight", action="store_true")
    p.add_argument("--fast", action="store_true",
                   help="seconds instead of minutes; smoke tests only")
    a = p.parse_args(argv)
    if a.fast:
        sys.stderr.write("WARNING: --fast yields smoke-test output, "
                         "not publishable results.\n")
    return run_cell(a, campaign)


if __name__ == "__main__":
    sys.exit(main())
