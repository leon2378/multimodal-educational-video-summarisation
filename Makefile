# Run from WSL2 (see README). Each target is a thin wrapper, so the commands also work on their own.
COMPOSE := docker compose -f infra/compose.yaml
ALEMBIC := uv run alembic -c packages/core/alembic.ini

.DEFAULT_GOAL := help
.PHONY: help install up app gpu-worker worker web openapi down reset migrate revision api process test test-unit lint fmt typecheck audit check

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install Python dependencies and git hooks
	uv sync
	uv run pre-commit install

up: ## Start Postgres, SeaweedFS and Temporal (UI on http://localhost:8233)
	$(COMPOSE) up -d --wait postgres seaweedfs temporal

app: ## Build and run the API, the web app and the CPU worker in Docker (http://localhost:3000)
	$(COMPOSE) --profile app up -d --build --wait

gpu-worker: ## Build and run the GPU worker (speech recognition) in Docker
	$(COMPOSE) --profile gpu up -d --build --wait gpu-worker

worker: ## Run a CPU worker on the host instead of in Docker
	uv run lecture-worker --queues cpu,llm

web: ## Run the web app with hot reload (http://localhost:3000); needs Node 24
	cd apps/web && corepack enable && pnpm install && pnpm run dev

openapi: ## Regenerate the web app's typed API client after changing the API
	uv run python -m lecture_api.openapi > apps/web/openapi.json
	cd apps/web && pnpm run gen:api

down: ## Stop everything, keeping data
	$(COMPOSE) --profile app --profile gpu down

reset: ## Stop everything and delete the data volumes
	$(COMPOSE) --profile app --profile gpu down --volumes

migrate: ## Apply database migrations
	$(ALEMBIC) upgrade head

revision: ## Generate a migration from model changes: make revision m="add chapters"
	@test -n "$(m)" || (echo 'usage: make revision m="describe the change"' && exit 1)
	$(ALEMBIC) revision --autogenerate -m "$(m)"
	uv run ruff format packages/core/migrations
	uv run ruff check --fix packages/core/migrations

api: ## Run the API on the host with auto-reload (http://localhost:8000/docs)
	uv run uvicorn lecture_api.main:create_app --factory --reload --port 8000

process: ## Run the pipeline on a local video without Temporal (GPU): make process video=... title=...
	@test -n "$(video)" || (echo 'usage: make process video=data/lectures/lecture.mp4 title="Title"' && exit 1)
	HOST_UID=$$(id -u) HOST_GID=$$(id -g) $(COMPOSE) --profile gpu run --rm --build pipeline "$(video)" --title "$(title)"

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
	uv export --locked --all-packages --all-extras --no-emit-workspace --format requirements-txt --output-file $$req -q && \
	uv run pip-audit --disable-pip --requirement $$req; status=$$?; rm -f $$req; exit $$status

check: lint typecheck test ## What CI runs, minus the image build
