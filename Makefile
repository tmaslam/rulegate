# ---------------------------------------------------------------------------
# {{PROJECT_NAME}} — task runner
#
# Placeholders: {{PROJECT_NAME}}, {{PACKAGE_NAME}}
#
# WINDOWS / GIT BASH
# ------------------
# GNU make is NOT installed by default on Windows, and Git Bash does not ship
# it. If `make` is not found, every target below has a one-line `uv run`
# equivalent — see `make help` or the table in README.md. Nothing here is
# make-only magic; make is a convenience, not a dependency.
#
# If you DO have make (via Chocolatey/Scoop/MSYS2), these rules assume Git Bash
# as the shell: forward slashes, POSIX utilities. That is what SHELL sets below.
#
# Traps this file deliberately avoids:
#   * No `$(shell ...)` calls that assume a Unix-only binary at parse time.
#   * No recursive `rm -rf` on a variable that could expand empty.
#   * No `.ONESHELL:` (behaves differently across make versions on Windows).
#   * Paths use forward slashes, which Git Bash and Windows both accept.
# ---------------------------------------------------------------------------

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# `demo` first so a bare `make` runs the thing a reviewer wants to see.
.DEFAULT_GOAL := help

DATASET ?= evals/datasets/golden.v1.jsonl
RUN_DIR ?= evals/runs
MAX_DROP ?= 0.03

.PHONY: help setup demo test eval lint fmt typecheck check clean docker-build

help: ## Show this help
	@echo "{{PROJECT_NAME}} — available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "No make on Windows? Use the uv equivalents:"
	@echo "  demo      ->  uv run python -m {{PACKAGE_NAME}}.demo"
	@echo "  test      ->  uv run pytest -m 'not live'"
	@echo "  eval      ->  uv run python -m evals.harness run --dataset $(DATASET) --out $(RUN_DIR)/head.json"
	@echo "  lint      ->  uv run ruff check . && uv run ruff format --check . && uv run mypy"

setup: ## Create the venv and install everything (uv handles Python 3.12 itself)
	uv sync --locked --dev
	uv run pre-commit install

demo: ## Run the demo. No API key, no network, no accounts.
	@echo "==> {{PROJECT_NAME}} demo — offline, zero-cost, deterministic fake provider."
	@echo "==> No .env required. Set provider keys only to exercise the live path."
	uv sync --locked --dev
	uv run python -m {{PACKAGE_NAME}}.demo

test: ## Run the test suite (excludes tests marked `live`)
	uv run pytest -m "not live"

eval: ## Run the golden-dataset eval suite against the deterministic fake
	uv run python -m evals.harness run \
		--dataset $(DATASET) \
		--out $(RUN_DIR)/head.json

eval-compare: ## Compare head vs base and apply the regression gate
	uv run python -m evals.harness compare \
		--base $(RUN_DIR)/base.json \
		--head $(RUN_DIR)/head.json \
		--max-drop $(MAX_DROP) \
		--markdown $(RUN_DIR)/comparison.md

lint: ## ruff check + ruff format --check + mypy strict
	uv run ruff check .
	uv run ruff format --check --diff .
	uv run mypy

fmt: ## Auto-fix lint and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy strict only
	uv run mypy

check: lint test eval ## Everything CI runs, in CI's order

clean: ## Remove caches and build artifacts (never touches evals/datasets)
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage coverage.xml
	rm -rf build dist
	rm -rf $(RUN_DIR)
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

docker-build: ## Build the image. Requires Docker (NOT installed on the dev box; CI builds it).
	docker build -t {{PROJECT_NAME}}:local .
