#!/usr/bin/env bash
# Full campaign driver. Reinstalls the datapath between arms, which is the only
# safe way to guarantee no residual state leaks between them.
set -euo pipefail

SMALL=0
[[ "${1:-}" == "--small" ]] && SMALL=1

if [[ $SMALL -eq 1 ]]; then
  DENSITIES=(10); PROFILES=(W1 W3); REPS=2; EXTRA="--fast"
else
  DENSITIES=(10 50 100 200); PROFILES=(W1 W2 W3 W4); REPS=10; EXTRA=""
fi

ARMS=("iptables:DP-IPT" "ipvs:DP-IPVS" "ebpf:DP-EBPF")
CAMPAIGN_ID="${CAMPAIGN_ID:-mtdp-$(date +%Y%m%d)}"
export CAMPAIGN_ID

total=$(( ${#ARMS[@]} * ${#DENSITIES[@]} * ${#PROFILES[@]} * REPS ))
echo "campaign=$CAMPAIGN_ID cells=$total"
[[ $SMALL -eq 1 ]] && echo "WARNING: --small output is a smoke test, not a result."

n=0
for arm in "${ARMS[@]}"; do
  IFS=: read -r installer dp <<<"$arm"
  echo "=== installing $dp ==="
  ./scripts/install-datapath.sh "$installer"
  kubectl apply -f deploy/harvester/rbac.yaml
  kubectl apply -f deploy/harvester/daemonset.yaml
  kubectl -n mtdp-system rollout status ds/mtdp-harvester --timeout=5m

  for d in "${DENSITIES[@]}"; do
    for p in "${PROFILES[@]}"; do
      for ((r=0; r<REPS; r++)); do
        n=$((n+1))
        echo "--- [$n/$total] $dp d=$d $p rep=$r ---"
        python3 orch/orchestrate.py \
          --datapath "$dp" --density "$d" --profile "$p" --rep "$r" \
          --campaign-id "$CAMPAIGN_ID" $EXTRA
      done
    done
  done
done

echo "=== analysing ==="
python3 nb/analyze.py --indir out --out out/results.json
python3 nb/validate_metrics.py --results out/results.json
echo "campaign complete -> out/results.json"
