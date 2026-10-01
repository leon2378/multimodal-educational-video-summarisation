# Run from WSL2 (see README). Each target is a thin wrapper, so the commands also work on their own.
# With an NVIDIA GPU (nvidia-smi finds one), the embedding model runs on it too
# (infra/compose.gpu.yaml). `make up GPU=` keeps it on the CPU.
GPU ?= $(shell nvidia-smi -L >/dev/null 2>&1 && echo 1)
# .env also fills in the Compose file's ${...} values (the web app's Clerk keys).
COMPOSE := docker compose $(if $(wildcard .env),--env-file .env) -f infra/compose.yaml $(if $(GPU),-f infra/compose.gpu.yaml)
ALEMBIC := uv run alembic -c packages/core/alembic.ini
# The cloud demo (ADR 0010): its project and region, from infra/terraform/cloud.tfvars.
TFVARS := infra/terraform/cloud.tfvars
PROJECT = $(shell sed -n 's/^project_id *= *"\(.*\)"/\1/p' $(TFVARS))
REGION = $(shell sed -n 's/^region *= *"\(.*\)"/\1/p' $(TFVARS))
TERRAFORM = terraform -chdir=infra/terraform/$(1)
TF_INIT = $(call TERRAFORM,$(1)) init -input=false -backend-config=bucket=$(PROJECT)-tfstate

.DEFAULT_GOAL := help
.PHONY: help install up app observability gpu-worker worker web openapi down reset migrate revision api process eval eval-retrieval detector-data detector-train bench modal-model modal cloud-base cloud-secrets cloud-seed deploy destroy test test-unit lint fmt typecheck audit check

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-15s %s\n", $$1, $$2}'

install: ## Install Python dependencies and git hooks
	uv sync
	uv run pre-commit install

up: ## Start Postgres, SeaweedFS, Temporal (UI on http://localhost:8233), Qdrant and the embedding server
	$(COMPOSE) up -d --wait postgres seaweedfs temporal qdrant embeddings

app: ## Build and run the API, the web app and the CPU worker in Docker (http://localhost:3000)
	$(COMPOSE) --profile app up -d --build --wait

observability: ## Start Grafana with traces, metrics and logs on http://localhost:3001
	$(COMPOSE) --profile observability up -d --wait otel-lgtm

gpu-worker: ## Build and run the GPU services in Docker: speech recognition and the reranker
	$(COMPOSE) --profile gpu up -d --build --wait gpu-worker reranker

worker: ## Run a CPU worker on the host instead of in Docker
	uv run lecture-worker --queues cpu,llm

web: ## Run the web app with hot reload (http://localhost:3000); needs Node 24
	cd apps/web && corepack enable && pnpm install && pnpm run dev

openapi: ## Regenerate the web app's typed API client after changing the API
	uv run python -m lecture_api.openapi > apps/web/openapi.json
	cd apps/web && pnpm run gen:api

down: ## Stop everything, keeping data
	$(COMPOSE) --profile app --profile gpu --profile observability down

reset: ## Stop everything and delete the data volumes
	$(COMPOSE) --profile app --profile gpu --profile observability down --volumes

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

eval: ## Run every eval suite against the running stack, record it, and check the thresholds
	uv run lecture-eval --gate

eval-retrieval: ## Score search only, on the golden Q&A set
	uv run lecture-eval --suites retrieval

detector-data: ## Label frames for the frame detector from the lectures' videos and slide PDFs
	uv sync --inexact --package lecture-detector --extra train
	uv run lecture-detector regions
	uv run lecture-detector dataset

detector-train: ## Fine-tune the frame detector on the GPU, then score the held-out lecture
	uv sync --inexact --package lecture-detector --extra train
	uv run lecture-detector train

bench: ## Benchmark embeddings, speech recognition and the detector, before and after (GPU stack)
	uv sync --inexact --package lecture-bench --extra export
	uv run lecture-bench embeddings
	uv run lecture-bench asr
	uv run lecture-bench detector

modal-model: ## Once, for the cloud demo: the speech model into a Modal Volume (needs `modal token new`)
	uv sync --inexact --package lecture-pipeline --extra modal
	uv run modal run infra/modal/asr.py::download_model

modal: ## Deploy speech recognition and the embedding model to GPUs in Modal (ADR 0010)
	uv sync --inexact --package lecture-pipeline --extra modal
	uv run modal deploy infra/modal/asr.py
	uv run modal deploy infra/modal/embeddings.py

cloud-base: ## Once: the demo's bucket, secrets, network and deploy access in Google Cloud
	@# What Terraform and the state bucket need first; Terraform switches on the rest. A project
	@# made by AI Studio for a Gemini key starts with only Gemini's API on.
	gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
		storage.googleapis.com --project $(PROJECT)
	gcloud auth application-default set-quota-project $(PROJECT)
	gcloud storage buckets describe gs://$(PROJECT)-tfstate --project $(PROJECT) > /dev/null 2>&1 || \
		gcloud storage buckets create gs://$(PROJECT)-tfstate --project $(PROJECT) --location $(REGION) \
			--uniform-bucket-level-access --public-access-prevention
	gcloud storage buckets update gs://$(PROJECT)-tfstate --versioning
	$(call TF_INIT,base)
	$(call TERRAFORM,base) apply -var-file=../cloud.tfvars -var state_bucket=$(PROJECT)-tfstate

cloud-secrets: ## Store infra/cloud.env (Gemini, Clerk and Modal keys) for the demo's VM
	gcloud secrets versions add lecture-demo-env --project $(PROJECT) --data-file=infra/cloud.env

cloud-seed: ## Copy the local stack's public lectures and stage cache to the demo's bucket
	uv run lecture-demo export data/demo-export
	gcloud storage rsync --recursive data/demo-export gs://$(PROJECT)-lectures

deploy: ## Make the demo's VM for a release (tag=v0.6.0); it's up about 10 minutes later
	@test -n "$(tag)" || (echo 'usage: make deploy tag=v0.6.0' && exit 1)
	$(call TF_INIT,demo)
	$(call TERRAFORM,demo) apply -var-file=../cloud.tfvars -var image_tag=$(tag)

destroy: ## Delete the demo's VM, and the session's uploads with it
	$(call TF_INIT,demo)
	$(call TERRAFORM,demo) destroy -var-file=../cloud.tfvars -var image_tag=none
	gcloud storage rm --recursive "gs://$(PROJECT)-lectures/raw/**" || true

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
