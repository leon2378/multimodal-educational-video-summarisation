# Lecture Summariser

Turns lecture videos into timestamp-grounded study notes and a Q&A chat whose answers cite the
moment in the lecture they come from.

**Status: Phase 1 of 6 (foundation).** The full design is in [docs/blueprint.md](docs/blueprint.md).
What exists today is described in [docs/architecture.md](docs/architecture.md).

## What works now

- A uv workspace with three packages: `apps/api` (FastAPI), `packages/core` (settings, models,
  storage), and `packages/pipeline` (the content-addressed stage cache).
- Direct-to-storage uploads: the API creates a lecture and hands out a presigned URL, the client
  uploads the file to storage, and the API confirms it.
- Postgres with Alembic migrations, and SeaweedFS as local S3, both in Docker Compose.
- Unit tests, plus integration tests that start real Postgres and SeaweedFS with testcontainers.
- CI: lint, type-check, tests, dependency audit, image build. Actions are pinned to commit SHAs.

## Getting started

Develop inside WSL2 (Ubuntu). Keep the repo on the Linux filesystem, e.g. `~/code/lecture-summariser`,
not under `/mnt/c/...`: cross-filesystem access is slow and breaks file watching.

You need:

- **Docker Desktop** with WSL integration turned on for Ubuntu (Settings → Resources → WSL integration)
- **uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **make**: `sudo apt install -y make`

Then:

```bash
make install   # Python deps (uv picks Python 3.12) and git hooks
make up        # Postgres and SeaweedFS
make migrate   # create the tables
make api       # API with auto-reload on http://localhost:8000 (docs at /docs)
```

No `.env` is needed: the defaults match the Compose stack. See [.env.example](.env.example) for
what can be changed.

### Try the upload flow

```bash
# 1. Create a lecture. The response includes a presigned upload URL.
curl -s -X POST localhost:8000/v1/lectures -H 'Content-Type: application/json' \
  -d '{"title": "Test lecture", "filename": "lecture.mp4", "content_type": "video/mp4"}' > created.json
ID=$(python3 -c 'import json; print(json.load(open("created.json"))["lecture"]["id"])')
URL=$(python3 -c 'import json; print(json.load(open("created.json"))["upload"]["url"])')

# 2. Upload the file straight to storage. The Content-Type must match what you declared.
curl -X PUT -H 'Content-Type: video/mp4' --data-binary @lecture.mp4 "$URL"

# 3. Confirm the upload. The status becomes "uploaded".
curl -s -X POST "localhost:8000/v1/lectures/$ID/complete-upload"
```

To browse stored files, open the SeaweedFS filer UI at http://localhost:8888.

To run the API in Docker instead of on the host, use `make app`. It builds the image, runs
migrations in a one-off container, and starts the API on port 8000.

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start or stop the backing services (`make reset` also deletes their data) |
| `make app` | Build and run the API in Docker alongside them |
| `make api` | Run the API on the host with auto-reload |
| `make migrate` | Apply migrations |
| `make revision m="add chapters"` | Generate a migration after changing `packages/core/src/lecture_core/models.py` |
| `make test` / `make test-unit` | All tests / unit tests only |
| `make lint` / `make fmt` / `make typecheck` | Ruff check / Ruff fix and format / mypy (strict) |
| `make audit` | pip-audit on the locked dependencies |
| `make check` | Lint, type-check and test, like CI |

## Layout

```
apps/api/                  FastAPI service (routes, schemas, dependencies)
packages/core/             settings, SQLAlchemy models, Alembic migrations, S3 client
packages/pipeline/         stage cache; stages and Temporal workflows from Phase 2
tests/unit/                fast tests, no Docker
tests/integration/         real Postgres and SeaweedFS via testcontainers
infra/compose.yaml         local stack (`app` profile adds the API)
infra/docker/              Dockerfiles
infra/seaweedfs/s3.json    dev-only S3 credentials
docs/                      blueprint, architecture, ADRs
```

## Tests

`make test` runs everything. Integration tests are marked `integration` and start their own
containers, so they need Docker but not `make up`. If Docker isn't running they're skipped
locally. In CI they must run and fail instead.

One integration test runs `alembic check`: it fails if a model changed without a migration.

## Decisions

Architecture decisions are recorded in [docs/adr](docs/adr/README.md). Phase 1 differs from the
blueprint in four places:

- **Temporal and Qdrant aren't in Compose yet.** They're added when first used: Temporal in Phase 2
  ([ADR 0002](docs/adr/0002-temporal-for-orchestration.md)), Qdrant in Phase 3.
- **Uploads are a single presigned PUT** (up to 5 GiB), not multipart. Multipart arrives with the
  web app and Uppy in Phase 2.
- **CI builds the image but doesn't scan or push it.** That comes with deployment in Phase 6.
- **No update bot (Dependabot or Renovate) opening PRs.** Dependencies are updated by hand with
  `uv lock --upgrade`, and CI audits the lockfile on every push. GitHub's Dependabot security
  alerts (Settings → Code security) still flag vulnerable packages without committing anything.

## Roadmap

- [x] **Phase 1, foundation**: workspace, Compose, API skeleton, migrations, stage cache, CI, first ADRs
- [ ] **Before Phase 2** (a few evenings):
  - [ ] Pick 3 slide-based MIT OCW lectures that have captions and slide PDFs. Many OCW lectures
        are chalkboard-only, which slide detection won't handle.
  - [ ] Run the single-call Gemini baseline on them to set the bar the pipeline has to beat.
  - [ ] Check faster-whisper int8 on the GPU inside Docker in WSL2, and note peak VRAM.
  - [ ] Draft 30 golden Q&A questions, so Phase 3 retrieval choices can be measured.
- [ ] **Phase 2, vertical slice**: ingest → ASR → slide changes → vision LLM → timeline → chapter
      summaries → lecture page
- [ ] **Phase 3, RAG Q&A**: hybrid search, reranking, streamed cited answers
- [ ] **Phase 4, evals and observability**: eval suites, Langfuse, OpenTelemetry, CI eval gate
- [ ] **Phase 5, CV and optimisation**: YOLO26 fine-tune, OCR-vs-VLM routing, ONNX/TensorRT/int8
- [ ] **Phase 6, ship**: auth, quotas, Terraform and Modal deploy, results write-up

## Data and licensing

Lecture videos never go in git (`.gitignore` blocks common video formats). MIT OCW material is
CC BY-NC-SA 4.0: record the licence and attribution on each lecture (the API has fields for both),
and keep the demo non-commercial. The repo itself has no licence yet. Choose one with the
YOLO26/RF-DETR decision in Phase 5, since YOLO26 is AGPL-3.0.
