# `sim/` -- discrete model, NOT measurement

**Read this before citing any number produced here.**

`mtdp_sim.py` is a parameterised analytical/queueing model of the three
datapaths. It is *not* a measurement of a real cluster. It exists for two
reasons:

1. To size the campaign before burning 16 machines for several days --
   choosing densities, run durations, and the number of repetitions needed for
   the isolation metrics to separate.
2. To provide a falsifiable prediction. When the physical campaign runs, any
   place where hardware disagrees with this model is a finding worth reporting,
   not an error to be tuned away.

The model is anchored on published figures and on the mechanism-level constants
documented in `docs/METHODOLOGY.md` (per-entry conntrack cost, LRU headroom,
softirq budget, and the queueing knee). Those anchors are assumptions.

Every record the model emits carries `"provenance": "model"`. Every record the
real harness emits carries `"provenance": "physical-cluster"`. `nb/analyze.py`
refuses to mix them in one table.

Run it:

    python3 sim/mtdp_sim.py --out out/
