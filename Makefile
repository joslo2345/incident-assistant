.DEFAULT_GOAL := help
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help env install hooks lint typecheck test test-integration schemas schemas-check dashboards check up down logs model mcp

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
	uv run mypy libs services replayer scripts tests

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

up: env  ## Build and start the local stack
	$(COMPOSE) up -d --build --wait

down:  ## Stop the local stack
	$(COMPOSE) down

logs:  ## Follow logs from the local stack
	$(COMPOSE) logs -f
