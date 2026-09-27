# Run from WSL2 (see README). Each target is a thin wrapper, so the commands also work on their own.
COMPOSE := docker compose -f infra/compose.yaml
ALEMBIC := uv run alembic -c packages/core/alembic.ini

.DEFAULT_GOAL := help
.PHONY: help install up app down reset migrate revision api test test-unit lint fmt typecheck audit check

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install Python dependencies and git hooks
	uv sync
	uv run pre-commit install

up: ## Start Postgres and SeaweedFS
	$(COMPOSE) up -d --wait postgres seaweedfs

app: ## Build and run the API in Docker too (http://localhost:8000/docs)
	$(COMPOSE) --profile app up -d --build --wait

down: ## Stop everything, keeping data
	$(COMPOSE) --profile app down

reset: ## Stop everything and delete the data volumes
	$(COMPOSE) --profile app down --volumes

migrate: ## Apply database migrations
	$(ALEMBIC) upgrade head

revision: ## Generate a migration from model changes: make revision m="add chapters"
	@test -n "$(m)" || (echo 'usage: make revision m="describe the change"' && exit 1)
	$(ALEMBIC) revision --autogenerate -m "$(m)"
	uv run ruff format packages/core/migrations
	uv run ruff check --fix packages/core/migrations

api: ## Run the API on the host with auto-reload (http://localhost:8000/docs)
	uv run uvicorn lecture_api.main:create_app --factory --reload --port 8000

test: ## Run all tests (integration tests need Docker)
	uv run pytest

test-unit: ## Run unit tests only
	uv run pytest -m "not integration"

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Type-check with mypy
	uv run mypy

audit: ## Check locked dependencies for known vulnerabilities
	@req=$$(mktemp) && \
	uv export --locked --no-emit-workspace --format requirements-txt --output-file $$req -q && \
	uv run pip-audit --disable-pip --requirement $$req; status=$$?; rm -f $$req; exit $$status

check: lint typecheck test ## What CI runs, minus the image build
