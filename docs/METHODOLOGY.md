# Methodology notes

Supplementary detail for reviewers and for anyone re-running the campaign.
This records the decisions that are easy to get wrong and that materially
change the numbers.

## 1. Why programming latency is polled, not self-reported

Each datapath publishes a metric that *sounds* like programming latency:

- kube-proxy: `sync_proxy_rules_duration_seconds` -- a whole-table resync, not
  a per-Service latency. Under 200 tenants this is dominated by rules unrelated
  to the Service that just changed.
- Cilium: `cilium_service_implementation_delay` -- per-Service, but measured
  from the agent's own receipt of the event, excluding the control-plane hop.

These are not the same quantity, so comparing them across arms is invalid. We
define one quantity and measure it identically everywhere
(`harv/probe_program_latency.py`):

    t0 = EndpointSlice becomes ready (control-plane accept)
    t1 = backend first resolvable in NODE-LOCAL datapath state

Polling costs quantisation. The interval is 2 ms and that floor is reported.
For DP-IPT at 200 tenants, medians are in the seconds, so the floor is
negligible; for DP-EBPF at 10 tenants, medians are ~31 ms, so the floor is
about 6% of the value and must be stated.

A vserver with zero real servers counts as *not programmed* -- traffic to it
blackholes. Counting it as programmed would flatter IPVS.

## 2. Open-loop load generation

A closed-loop generator (send, wait for reply, send again) silently reduces
offered load when the system under test slows down. That converts a latency
regression into a throughput reduction and hides exactly the effect we are
looking for. `images/churngen/churngen.py` schedules against absolute
wall-clock deadlines, so backlog accumulates and appears in the tail.

Latency samples are reservoir-sampled (cap 200,000 per worker) rather than
fully retained, to bound generator memory without biasing the distribution.

## 3. Conntrack sizing and the FM-1 failure mode

The most consequential single constant. At 200 tenants under W4 the per-node
demand is roughly 2.95M concurrent entries. With the common default of
`nf_conntrack_max = 1048576`, the netfilter arms overflow and their connection
failure rate exceeds the eBPF arm's -- which inverts the paper's central
failure-mode narrative. That is a property of the *default*, not of the
datapath.

We therefore set `nf_conntrack_max = 4194304` with 1048576 buckets
(`config/cluster.yaml`) so netfilter is *not* the bottleneck, and the eBPF LRU
eviction behaviour is isolated as the genuine finding. Cilium's
`bpf.ctTcpMax` default of 524288 is swept against 2097152, and the memory cost
of the tuned setting (+465 MB/node) is reported alongside the failure-rate
improvement, because that trade-off is the actual result.

The sysctl DaemonSet is re-applied after every datapath install. Cilium and
kube-proxy both write conntrack sysctls at startup.

## 4. Teardown discipline

Deleting namespaces returns immediately; conntrack entries persist in TIME_WAIT
for much longer. The orchestrator blocks until the table drains below 50,000
before the next cell starts. Without this, conntrack utilisation for run N+1
includes residue from run N, and the effect compounds across a 480-run campaign.

## 5. Statistics

Ten repetitions per (datapath x density x profile). The first 120 s of every
run is discarded as warm-up. Comparisons use Mann-Whitney U (alpha = 0.05) with
Holm-Bonferroni correction across the family, and Cliff's delta for effect
size; the distributions are heavily skewed, so a t-test would be inappropriate.

`nb/ministats.py` implements these without SciPy so the analysis runs with a
bare Python install.

## 6. What this harness does not measure

- No L7 policy, no encryption, no service mesh.
- No hardware attribution: we do not use Intel RDT or `perf` counters, so
  "interference" is observed as black-box degradation and not attributed to a
  specific shared resource (LLC, memory bandwidth, or NIC queue).
- One eBPF implementation as the primary arm (Cilium), with Calico eBPF only as
  a cross-validation point.
