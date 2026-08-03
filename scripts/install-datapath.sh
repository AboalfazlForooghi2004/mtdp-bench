#!/usr/bin/env bash
# Install exactly one datapath arm and record which one, so the orchestrator's
# preflight can detect a mislabelled run.
set -euo pipefail

ARM="${1:-}"
CILIUM_VERSION="${CILIUM_VERSION:-1.16.5}"
API_HOST="${API_HOST:-$(kubectl get endpoints kubernetes -o jsonpath='{.subsets[0].addresses[0].ip}')}"
API_PORT="${API_PORT:-6443}"

usage() { echo "usage: $0 {iptables|ipvs|ebpf|ebpf-ct-tuned|calico}" >&2; exit 2; }
[[ -n "$ARM" ]] || usage

purge_cilium() {
  helm uninstall cilium -n kube-system 2>/dev/null || true
  kubectl -n kube-system delete ds cilium cilium-envoy 2>/dev/null || true
}

purge_kube_proxy() {
  kubectl -n kube-system delete ds kube-proxy 2>/dev/null || true
}

case "$ARM" in
  iptables|ipvs)
    purge_cilium
    kubectl apply -f "deploy/datapaths/kube-proxy-${ARM/ipvs/ipvs}.yaml" 2>/dev/null \
      || kubectl apply -f "deploy/datapaths/kube-proxy-iptables.yaml"
    if [[ "$ARM" == "ipvs" ]]; then
      kubectl apply -f deploy/datapaths/kube-proxy-ipvs.yaml
      # IPVS needs the kernel modules present on every node.
      for m in ip_vs ip_vs_rr ip_vs_wrr ip_vs_sh nf_conntrack; do
        echo "ensure module: $m"
      done
      DP="DP-IPVS"
    else
      DP="DP-IPT"
    fi
    kubectl -n kube-system rollout restart ds/kube-proxy
    kubectl -n kube-system rollout status ds/kube-proxy --timeout=5m
    ;;
  ebpf|ebpf-ct-tuned)
    purge_kube_proxy
    VALUES=deploy/datapaths/cilium-values.yaml
    [[ "$ARM" == "ebpf-ct-tuned" ]] && VALUES=deploy/datapaths/cilium-values-ct-tuned.yaml
    helm repo add cilium https://helm.cilium.io/ >/dev/null
    helm repo update >/dev/null
    helm upgrade --install cilium cilium/cilium \
      --version "$CILIUM_VERSION" \
      --namespace kube-system \
      --values "$VALUES" \
      --set k8sServiceHost="$API_HOST" \
      --set k8sServicePort="$API_PORT" \
      --wait --timeout 10m
    DP="DP-EBPF"
    ;;
  calico)
    purge_kube_proxy; purge_cilium
    kubectl apply -f deploy/datapaths/calico-values.yaml
    DP="DP-CALICO-BPF"
    ;;
  *) usage ;;
esac

# Apply the shared sysctl invariants AFTER the datapath, because some installers
# reset nf_conntrack_max. Skipping this silently invalidates the comparison.
kubectl apply -f deploy/datapaths/node-tuning-daemonset.yaml
kubectl -n mtdp-system rollout status ds/mtdp-node-tuning --timeout=5m

kubectl -n mtdp-system create configmap mtdp-run \
  --from-literal=datapath="$DP" \
  --from-literal=campaign="${CAMPAIGN_ID:-dev}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "installed arm=$ARM datapath=$DP"
