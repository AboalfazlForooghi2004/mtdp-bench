#!/usr/bin/env python3
"""Render and apply the tenant population for one campaign run.

Reads config/tenant_mix.yaml, expands it to `density` tenants using a seeded
RNG, renders deploy/tenant/templates/tenant.yaml.j2 once per tenant, and either
writes the manifests to disk or pipes them to `kubectl apply`.

The seeding matters. The archetype assignment must be identical across the
three datapath arms, otherwise the arms are not comparable: a run where
DP-EBPF happened to get more T-Bulk tenants would look worse for reasons that
have nothing to do with eBPF. We derive the assignment from (seed, density)
only, never from the datapath.
"""
from __future__ import annotations

import argparse
import pathlib
import random
import subprocess
import sys

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "deploy" / "tenant" / "templates"


def load_mix(path):
    mix = yaml.safe_load(path.read_text())
    total = sum(a["share"] for a in mix["archetypes"])
    if abs(total - 1.0) > 1e-9:
        raise SystemExit("tenant_mix shares sum to %s, expected 1.0" % total)
    return mix


def assign_archetypes(mix, density, seed):
    """Largest-remainder allocation, then a seeded shuffle.

    Largest-remainder rather than rounding, so the counts always sum to exactly
    `density`. At density=10 a naive round() yields 9 or 11 tenants and the
    '10-tenant' run silently is not a 10-tenant run.
    """
    arch = mix["archetypes"]
    exact = [a["share"] * density for a in arch]
    counts = [int(x) for x in exact]
    remainder = density - sum(counts)
    order = sorted(range(len(arch)), key=lambda i: exact[i] - counts[i], reverse=True)
    for i in range(remainder):
        counts[order[i % len(order)]] += 1

    tenants = []
    for a, n in zip(arch, counts):
        for _ in range(n):
            tenants.append(dict(a))
    # Seed depends on density but NOT on datapath, so arms stay comparable.
    random.Random("%s:%s" % (seed, density)).shuffle(tenants)
    for i, t in enumerate(tenants):
        t["tenant_id"] = "t%04d" % i
    return tenants


def render(tenants, mix, args):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template("tenant.yaml.j2")
    objs = mix["per_tenant_objects"]
    docs = []
    for t in tenants:
        docs.append(
            tpl.render(
                ns="mtdp-" + t["tenant_id"],
                tenant_id=t["tenant_id"],
                archetype=t["name"],
                role=t.get("role", "neutral"),
                campaign_id=args.campaign_id,
                registry=args.registry,
                image_tag=args.image_tag,
                replicas=t.get("replicas", 2),
                services=objs["services"],
                network_policies=objs["network_policies"],
                payload_bytes=args.payload_bytes,
                cpu_request="50m",
                cpu_limit="500m",
                quota_cpu_requests="4",
                quota_mem_requests="4Gi",
                quota_cpu_limits="8",
                quota_mem_limits="8Gi",
            )
        )
    return "\n".join(docs)


def main(argv=None):
    p = argparse.ArgumentParser(description="Provision the MTDP tenant population")
    p.add_argument("--density", type=int, required=True, choices=[10, 50, 100, 200])
    p.add_argument("--seed", type=int, default=20260802)
    p.add_argument("--campaign-id", default="dev")
    p.add_argument("--registry", default="ghcr.io/aboalfazlforooghi2004")
    p.add_argument("--image-tag", default="v0.1.0")
    p.add_argument("--payload-bytes", type=int, default=1024)
    p.add_argument("--mix", type=pathlib.Path,
                   default=ROOT / "config" / "tenant_mix.yaml")
    p.add_argument("-o", "--out", type=pathlib.Path,
                   help="write manifests here instead of printing")
    p.add_argument("--apply", action="store_true", help="pipe to kubectl apply -f -")
    p.add_argument("--summary", action="store_true",
                   help="print the archetype histogram and exit")
    a = p.parse_args(argv)

    mix = load_mix(a.mix)
    tenants = assign_archetypes(mix, a.density, a.seed)

    if a.summary:
        hist = {}
        for t in tenants:
            hist[t["name"]] = hist.get(t["name"], 0) + 1
        print("density=%d seed=%d total=%d" % (a.density, a.seed, len(tenants)))
        for k in sorted(hist):
            print("  %-12s %4d  (%.1f%%)" % (k, hist[k], 100.0 * hist[k] / len(tenants)))
        return 0

    manifests = render(tenants, mix, a)

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(manifests)
        sys.stderr.write("wrote %d tenants -> %s\n" % (len(tenants), a.out))
    if a.apply:
        proc = subprocess.run(["kubectl", "apply", "-f", "-"],
                              input=manifests, text=True)
        return proc.returncode
    if not a.out:
        sys.stdout.write(manifests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
