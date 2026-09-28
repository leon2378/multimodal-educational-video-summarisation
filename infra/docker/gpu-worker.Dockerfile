# syntax=docker/dockerfile:1
# GPU worker image: the pipeline with faster-whisper and NVIDIA's CUDA 12 libraries, taken from
# PyPI rather than a multi-GB CUDA base image. Needs an NVIDIA driver on the host and
# `--gpus all` (or the compose `gpu` profile).
# Build from the repo root: docker build -f infra/docker/gpu-worker.Dockerfile .

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# uv needs every workspace member's pyproject.toml to resolve the lockfile, even unused ones.
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/
COPY evals/pyproject.toml evals/
COPY packages/core/pyproject.toml packages/core/
COPY packages/llm/pyproject.toml packages/llm/
COPY packages/perception/pyproject.toml packages/perception/
COPY packages/pipeline/pyproject.toml packages/pipeline/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-pipeline --extra gpu --no-install-workspace

COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-pipeline --extra gpu --no-editable


FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY prompts prompts
# CTranslate2 finds cuBLAS and cuDNN through LD_LIBRARY_PATH, which must be set before Python
# starts. HOME=/tmp because compose may run this as your host user, who has no home here.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    LD_LIBRARY_PATH="/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib"
USER app
ENTRYPOINT ["lecture-process"]
