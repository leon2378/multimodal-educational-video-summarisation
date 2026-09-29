# syntax=docker/dockerfile:1
# Pipeline worker image, in two variants (build from the repo root):
#   CPU (default), for the cpu and llm task queues:
#     docker build -f infra/docker/worker.Dockerfile -t lecture-summariser/worker:cpu .
#   GPU, adding faster-whisper and NVIDIA's CUDA 12 libraries (from PyPI rather than a multi-GB
#   CUDA base image), for the gpu queue and `lecture-process`. Needs an NVIDIA driver and
#   `--gpus all`:
#     docker build -f infra/docker/worker.Dockerfile --build-arg EXTRA=gpu \
#       -t lecture-summariser/worker:gpu .

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim AS build
ARG EXTRA=""
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# uv needs every workspace member's pyproject.toml to resolve the lockfile, even unused ones.
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/
COPY evals/pyproject.toml evals/
COPY ml/detector/pyproject.toml ml/detector/
COPY packages/core/pyproject.toml packages/core/
COPY packages/llm/pyproject.toml packages/llm/
COPY packages/perception/pyproject.toml packages/perception/
COPY packages/pipeline/pyproject.toml packages/pipeline/
COPY packages/rag/pyproject.toml packages/rag/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-pipeline ${EXTRA:+--extra $EXTRA} --no-install-workspace

COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package lecture-pipeline ${EXTRA:+--extra $EXTRA} --no-editable


FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY prompts prompts
# CTranslate2 finds cuBLAS and cuDNN through LD_LIBRARY_PATH, which must be set before Python
# starts (harmless in the CPU image, where the paths don't exist). HOME=/tmp because compose
# may run this as your host user, who has no home here.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    LD_LIBRARY_PATH="/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib"
USER app
CMD ["lecture-worker", "--queues", "cpu,llm"]
