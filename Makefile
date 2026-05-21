.PHONY: help install fmt lint typecheck test eval eval-smoke demo clean docker-up docker-down

PYTHON ?= python3.12
VENV   ?= .venv
BIN    := $(VENV)/bin

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install dev deps
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip wheel
	$(BIN)/pip install -e ".[dev]"
	$(BIN)/pre-commit install
	@echo "✓ install complete — activate with: source $(VENV)/bin/activate"

fmt: ## Format code
	$(BIN)/ruff format src/ eval/ tests/
	$(BIN)/ruff check --fix src/ eval/ tests/

lint: ## Lint
	$(BIN)/ruff check src/ eval/ tests/
	$(BIN)/ruff format --check src/ eval/ tests/

typecheck: ## mypy
	$(BIN)/mypy src/ eval/

test: ## Run unit tests
	$(BIN)/pytest tests/unit -q

test-all: ## Run all tests including integration
	$(BIN)/pytest tests/ -q

eval-smoke: ## 30-incident smoke eval (CI)
	$(BIN)/python -m eval.runner --suite smoke --report eval/REPORT_smoke.md

eval: ## Full benchmark eval (220+ incidents, 3 seeds)
	$(BIN)/python -m eval.runner --suite full --seeds 3 --report eval/REPORT.md

demo: ## Run scripted demo
	$(BIN)/python -m dq_triage.cli demo

docker-up: ## Start postgres + grafana
	docker compose up -d

docker-down:
	docker compose down

hello: ## Sanity check
	@echo "ok"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache .llm_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
