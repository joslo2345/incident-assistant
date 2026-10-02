.DEFAULT_GOAL := help
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help env install hooks lint typecheck test test-integration schemas schemas-check dashboards check up down logs model mcp demo-users kind-up kind-install kind-down web-dev eval eval-data eval-ci eval-ci-record eval-calibrate

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install:  ## Install all workspace packages and dev tools
	uv sync

hooks:  ## Install git pre-commit hooks
	uvx pre-commit install

lint:  ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

typecheck:  ## Strict mypy
	uv run mypy libs services harness replayer scripts tests

test:  ## Run unit tests
	uv run pytest

test-integration:  ## Run integration tests against the running stack (make up first)
	INTEGRATION=1 uv run pytest tests/integration -v -s

dashboards:  ## Regenerate Grafana dashboards from scripts/build_dashboards.py
	uv run python scripts/build_dashboards.py

schemas:  ## Regenerate docs/schemas from code
	uv run python scripts/export_schemas.py

schemas-check:  ## Fail if docs/schemas is stale
	uv run python scripts/export_schemas.py --check

check: lint typecheck test schemas-check  ## Everything CI runs, except Docker builds

env:  ## Generate deploy/.env with random local secrets (once)
	@uv run python scripts/gen_env.py

model:  ## Build the local agent model in Ollama (brew install ollama; ~18 GB download)
	ollama pull qwen3:30b-a3b
	ollama create qwen3-agent -f deploy/ollama/Modelfile

mcp:  ## Serve the agent's tools over MCP (stdio) against the local stack
	@uv run python scripts/agent_env.py uv run agent mcp

SET ?= dev
LABEL ?=

eval-data:  ## Replay labelled faults and build the eval sets (dev, test); ~15 min
	bash -c 'for spec in "a6dev 2d 25 51 all" "a6devnn 1d 8 61 noisy_neighbor" "a6test 2d 25 53 all" \
	  "a6testnn 1d 8 63 noisy_neighbor"; do set -- $$spec; \
	  ft=$$([ "$$5" = all ] || echo "--fault-types $$5"); \
	  uv run python scripts/eval_detection.py --run-id $$1 --duration $$2 --faults $$3 --seed $$4 \
	    --bmc-dropout 0.5 $$ft > /dev/null || exit 1; done'
	uv run evaluate build --name dev --run-id a6dev --run-id a6devnn --max-decoys 3
	uv run evaluate build --name test --run-id a6test --run-id a6testnn --max-decoys 3

eval:  ## Score the agent on an eval set with the local model and judge (SET=dev|test, LABEL=name)
	uv run evaluate run --set $(SET) --judge $(if $(LABEL),--label $(LABEL))

eval-ci:  ## CI eval: replay the 10 CI faults under a fresh run id, then the recorded model replies
	@RUN=ci$$(date +%s | tail -c 7); echo "eval-ci run id: $$RUN"; \
	uv run python scripts/eval_detection.py --run-id $$RUN --duration 1d --faults 10 --seed 7 \
	  --bmc-dropout 0.5 > /dev/null && \
	uv run evaluate build --name ci-replay --run-id $$RUN && \
	uv run evaluate run --set ci-replay --replay eval/cassettes/ci.json \
	  --gate eval/cassettes/ci.expected.json --label replay --out eval/reports/a6/ci-replay

eval-ci-record:  ## Re-record the model replies the CI eval replays (after changing the agent; ~30 min)
	@RUN=ci$$(date +%s | tail -c 7); echo "recording run id: $$RUN"; \
	uv run python scripts/eval_detection.py --run-id $$RUN --duration 1d --faults 10 --seed 7 \
	  --bmc-dropout 0.5 > /dev/null && \
	uv run evaluate build --name ci-replay --run-id $$RUN && \
	uv run evaluate run --set ci-replay --record eval/cassettes/ci.json --label recording \
	  --out eval/reports/a6/ci-recording

eval-calibrate:  ## Judge agreement with hand grades (REPORT=eval/reports/a6/dev/<label>.json)
	uv run evaluate calibrate $(REPORT) --out eval/judge/calibration.json

demo-users:  ## Create local accounts alice (approver) and victor (viewer); passwords in deploy/.env
	@set -a; . deploy/.env; set +a; \
	AGENT_NEW_USER_PASSWORD="$$DEMO_APPROVER_PASSWORD" uv run python scripts/agent_env.py \
	  uv run agent users add alice --role approver --name "Alice (on-call)" --slack U0DEMOALICE && \
	AGENT_NEW_USER_PASSWORD="$$DEMO_VIEWER_PASSWORD" uv run python scripts/agent_env.py \
	  uv run agent users add victor --role viewer --name "Victor (viewer)"

web-dev:  ## Run the web UI with hot reload on http://localhost:5173 (stack must be up)
	cd web && npm install && npm run dev

KIND := incident-assistant
SERVICES := ingest consumer detector knowledge agent web

CALICO_VERSION := v3.33.0
CALICO_SHA256 := 2de8f47595fb9c41b3f47d7b767a1f8e72ecf84057af834738ff12689a234da5

kind-up:  ## Local Kubernetes (kind + Calico, which enforces NetworkPolicy)
	kind create cluster --config deploy/kind/cluster.yaml
	@# Pinned and checksum-verified; the pod CIDR must match deploy/kind/cluster.yaml.
	curl -fsSL https://raw.githubusercontent.com/projectcalico/calico/$(CALICO_VERSION)/manifests/calico.yaml \
	  -o /tmp/calico-$(CALICO_VERSION).yaml
	echo "$(CALICO_SHA256)  /tmp/calico-$(CALICO_VERSION).yaml" | shasum -a 256 -c -
	sed -e 's|# - name: CALICO_IPV4POOL_CIDR|- name: CALICO_IPV4POOL_CIDR|' \
	  -e 's|#   value: "192.168.0.0/16"|  value: "10.244.0.0/16"|' /tmp/calico-$(CALICO_VERSION).yaml \
	  | kubectl apply -f - > /dev/null
	kubectl -n kube-system rollout status ds/calico-node --timeout=300s

kind-install: env  ## Build images, load them into kind, create the Secret, helm install
	docker compose -f deploy/compose.yaml build $(SERVICES)
	for s in $(SERVICES); do kind load docker-image incident-assistant/$$s:dev --name $(KIND); done
	kubectl create namespace ia --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n ia create secret generic incident-assistant-secrets --from-env-file=deploy/.env \
	  --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install ia deploy/helm/incident-assistant -n ia -f deploy/kind/values.yaml \
	  --wait --wait-for-jobs --timeout 15m

kind-down:  ## Delete the local Kubernetes cluster
	kind delete cluster --name $(KIND)

up: env  ## Build and start the local stack
	$(COMPOSE) up -d --build --wait

down:  ## Stop the local stack
	$(COMPOSE) down

logs:  ## Follow logs from the local stack
	$(COMPOSE) logs -f
