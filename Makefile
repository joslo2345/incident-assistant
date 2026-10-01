.DEFAULT_GOAL := help
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help install hooks lint typecheck test schemas schemas-check check up down logs

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
	uv run mypy libs services scripts tests

test:  ## Run tests
	uv run pytest

schemas:  ## Regenerate docs/schemas from code
	uv run python scripts/export_schemas.py

schemas-check:  ## Fail if docs/schemas is stale
	uv run python scripts/export_schemas.py --check

check: lint typecheck test schemas-check  ## Everything CI runs, except Docker builds

up:  ## Build and start the local stack
	$(COMPOSE) up -d --build --wait

down:  ## Stop the local stack
	$(COMPOSE) down

logs:  ## Follow logs from the local stack
	$(COMPOSE) logs -f
