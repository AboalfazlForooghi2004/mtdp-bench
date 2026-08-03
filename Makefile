# MTDP-Bench
SHELL := /bin/bash
REGISTRY ?= ghcr.io/aboalfazlforooghi2004
TAG      ?= v0.1.0
DENSITY  ?= 10
PROFILE  ?= W1
DATAPATH ?= DP-EBPF

.PHONY: help test lint render images push kind-up kind-down install-harvester \
        install-ipt install-ipvs install-ebpf campaign campaign-small figures clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

test: ## Run unit tests (stdlib only, no pip install required)
	python3 -m unittest discover -s nb/tests -t . -v

lint: ## Byte-compile all Python and parse all YAML
	python3 -m compileall -q images prov orch harv nb sim
	python3 scripts/validate_yaml.py

render: ## Render tenant manifests without applying them
	python3 prov/provision.py --density $(DENSITY) --summary
	python3 prov/provision.py --density $(DENSITY) -o out/tenants-d$(DENSITY).yaml

images: ## Build all container images (loadgen context = repo root)
	docker build -f images/echo-backend/Dockerfile -t $(REGISTRY)/mtdp-echo-backend:$(TAG) images/echo-backend
	docker build -f images/churngen/Dockerfile     -t $(REGISTRY)/mtdp-churngen:$(TAG)     images/churngen
	docker build -f images/harvester/Dockerfile    -t $(REGISTRY)/mtdp-harvester:$(TAG)    images/harvester
	docker build -f images/loadgen/Dockerfile      -t $(REGISTRY)/mtdp-loadgen:$(TAG)      .

push: images ## Push images to the registry
	for i in echo-backend churngen harvester loadgen; do \
	  docker push $(REGISTRY)/mtdp-$$i:$(TAG); done

kind-up: ## 4-node kind cluster for smoke testing (NOT for results)
	kind create cluster --config deploy/kind/kind-cluster.yaml --name mtdp

kind-down:
	kind delete cluster --name mtdp

install-ipt: ## Install the DP-IPT arm
	./scripts/install-datapath.sh iptables

install-ipvs: ## Install the DP-IPVS arm
	./scripts/install-datapath.sh ipvs

install-ebpf: ## Install the DP-EBPF arm (Cilium, kube-proxy replaced)
	./scripts/install-datapath.sh ebpf

install-harvester: ## Deploy the node-local state harvester and sysctl tuning
	kubectl apply -f deploy/harvester/rbac.yaml
	kubectl apply -f deploy/harvester/daemonset.yaml
	kubectl apply -f deploy/datapaths/node-tuning-daemonset.yaml

campaign: ## Full campaign: 3 datapaths x 4 densities x 4 profiles x 10 reps
	./scripts/run-campaign.sh

campaign-small: ## Reduced smoke campaign for kind
	./scripts/run-campaign.sh --small

figures: ## Regenerate the paper figures
	python3 nb/figures.py --results out/results.json --outdir figs

clean:
	rm -rf out figs/*.png .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
