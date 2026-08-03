# MTDP-Bench

A reproducible benchmark harness for comparing **eBPF and traditional Linux
datapaths in multi-tenant Kubernetes clusters** across performance,
scalability, and resource-isolation trade-offs.

Artifact for the paper *"eBPF vs Traditional Datapaths in Multi-Tenant
Kubernetes Environments: An Empirical Study of Performance, Scalability, and
Resource Isolation Trade-offs."*

---

## Read this first: provenance of the numbers

The results currently reported in the paper are produced by the **model** in
`sim/`, not by a physical cluster. They are clearly marked as such in the paper
(Limitation L10) and in `sim/README.md`.

Everything else in this repository -- the provisioner, the load generators, the
node-local state harvester, the datapath installers, the manifests and the
container images -- is the **real measurement harness**, written to run against
real hardware. It has not yet been executed on the 16-node testbed described in
the paper.

`nb/analyze.py` tags every record with `provenance` (`model` or
`physical-cluster`) and refuses to combine the two in one table. Please keep
that guarantee intact if you extend this work.

---

## What is being compared

| Arm | Datapath | Service implementation | Connection tracking |
|---|---|---|---|
| `DP-IPT` | kube-proxy, iptables mode | linear-ish `nat` chains | `nf_conntrack` |
| `DP-IPVS` | kube-proxy, IPVS mode | IPVS hash + real servers | `nf_conntrack` |
| `DP-EBPF` | Cilium 1.16.x, kube-proxy replaced | `lb4` BPF maps | BPF LRU CT maps |
| `DP-CALICO-BPF` | Calico eBPF (cross-validation) | Calico BPF | BPF CT |

Four workload profiles: **W1** steady state, **W2** service churn (the
`rule_program_ms` probe), **W3** aggressor injection (the isolation metrics),
**W4** connection-churn stress (the conntrack failure mode).

Four tenant densities: 10, 50, 100, 200.

---

## Repository layout

    config/         campaign, tenant mix, and cluster invariants (all YAML)
    prov/           deterministic tenant-population provisioner
    deploy/
      datapaths/    kube-proxy configs, Cilium/Calico Helm values, sysctl DaemonSet
      tenant/       Jinja2 template for one tenant namespace
      harvester/    privileged node-local state collector (RBAC + DaemonSet)
      observability/ InfluxDB for time-series capture
      kind/         smoke-test cluster (NOT valid for results)
    images/         echo-backend, churngen, harvester, loadgen + Dockerfiles
    orch/           campaign orchestrator (preflight -> provision -> measure -> drain)
    harv/           per-Service programming-latency probe
    nb/             isolation metrics, statistics, analysis, figures + unit tests
    sim/            the model (see sim/README.md -- not measurement)
    scripts/        datapath installer, campaign driver, YAML validator

---

## Quick start

No dependencies beyond Python 3.10+, `pyyaml` and `jinja2` are needed to verify
the harness itself:

    make test        # unit tests for the isolation metrics and statistics
    make lint        # byte-compile all Python, parse all YAML
    make render      # render tenant manifests for inspection, without applying

Against a real cluster:

    make images push
    make install-ebpf
    make install-harvester
    make campaign

Against kind, for a smoke test only:

    make kind-up install-ebpf install-harvester campaign-small

> kind nodes share one kernel and therefore one conntrack table. Per-node state
> isolation -- the central mechanism this study measures -- does not exist
> there. Use kind to check that the harness runs, never to produce results.

---

## Design principles

**P1 -- datapath neutrality.** Nothing may advantage one arm. Hubble is disabled
so Cilium is not charged for observability the other arms do not provide.
Programming latency is measured by polling node-local datapath state under one
common definition, rather than by reading each project's own metric, because
those metrics measure different things. `nf_conntrack_max` and the other sysctl
invariants in `config/cluster.yaml` are re-applied *after* every datapath
install, because some installers reset them.

**P2 -- tenant realism.** Tenants are not identical. The population mixes four
archetypes (`config/tenant_mix.yaml`): T-Web (50%), T-Churn (20%), T-Bulk (20%,
the aggressor), T-Latency (10%, the victim). Load generation is **open-loop**
with absolute scheduling, so a slow datapath produces queueing rather than
silently reducing offered load.

**P3 -- reproducibility by construction.** The provisioner is seeded on
`(seed, density)` and deliberately *not* on the datapath, so all three arms see
a byte-identical tenant population. CI asserts this. The orchestrator refuses
to run if it detects a datapath other than the one named on the command line,
and blocks on conntrack draining between runs so one run cannot contaminate the
next.

---

## The isolation metrics

`nb/isolation.py` implements the three metrics used in the paper. They are
**adaptations of established measures, not inventions**; the provenance of each
is given in Table 2 of the paper and in the module docstrings.

- **IR (Interference Ratio)** -- median relative degradation of non-aggressor
  tenants under aggression, in the tradition of normalized-slowdown metrics.
- **FD (Fairness Deviation)** -- departure from proportional share; algebraically
  related to Jain's fairness index, which the tests verify.
- **BR (Blast Radius)** -- fraction of non-aggressor tenants degraded past a
  threshold, a tail-impact count in the spirit of *The Tail at Scale*.

The threshold in BR is a policy choice, so the paper reports a sensitivity
sweep over it rather than a single value.

---

## Reproducing the figures

    python3 sim/mtdp_sim.py --out out/
    python3 nb/validate_metrics.py --results out/results.json
    python3 nb/figures.py --results out/results.json --outdir figs

---

## Citing

See `CITATION.cff`.

## License

Apache-2.0. See `LICENSE`.
