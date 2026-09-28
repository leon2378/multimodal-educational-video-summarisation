# syntax=docker/dockerfile:1
# API image. Build from the repo root: docker build -f infra/docker/api.Dockerfile .

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Third-party dependencies first, so source edits don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
# uv needs every workspace member's pyproject.toml to resolve the lockfile, even unused ones.
COPY apps/api/pyproject.toml apps/api/
COPY evals/pyproject.toml evals/
COPY packages/core/pyproject.toml packages/core/
COPY packages/llm/pyproject.toml packages/llm/
COPY packages/perception/pyproject.toml packages/perception/
COPY packages/pipeline/pyproject.toml packages/pipeline/
COPY packages/rag/pyproject.toml packages/rag/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-api --no-install-workspace

# Then the workspace packages, installed as wheels so the runtime image needs no source tree.
COPY apps/api apps/api
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-api --no-editable


FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations run from this image too (the `migrate` service in infra/compose.yaml).
COPY packages/core/alembic.ini packages/core/alembic.ini
COPY packages/core/migrations packages/core/migrations
# Q&A prompts.
COPY prompts/qa prompts/qa
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "lecture_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
