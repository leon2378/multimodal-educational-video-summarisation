# Lecture Summariser

Turns lecture videos into timestamp-grounded study notes and a Q&A chat whose answers cite the
moment in the lecture they come from.

**Status: Phases 1 to 3 of 6 done: upload a lecture in the browser, watch it process, then study it with a synced transcript, slides, chapters and notes, search it, and ask questions whose answers cite the moments they come from, about one lecture or a whole course. Evals and observability come next.** The full design is in [docs/blueprint.md](docs/blueprint.md).
What exists today is described in [docs/architecture.md](docs/architecture.md).

## What works now

- A uv workspace: `apps/api` (FastAPI), `packages/core` (settings, models, storage, the timeline
  and study-notes formats), `packages/perception` (video, audio, slide detection, speech
  recognition), `packages/llm` (Pydantic AI agents for the pipeline and Q&A), `packages/pipeline` (stages, stage cache,
  local runner), `packages/rag` (chunking, embeddings, the search index, hybrid search) and
  `evals` (baselines, golden sets and scoring).
- A web app ([below](#web-app)): upload with a progress bar, live processing progress, and a
  lecture page with the video, a transcript that follows playback, slides, chapters, notes, a
  quiz, search and a Q&A chat, every timestamp clickable.
- Processing through the API: a Temporal workflow per lecture, CPU and GPU workers, progress
  over server-sent events, and results in Postgres ([below](#processing-a-lecture)).
- The processing pipeline: speech recognition, slide detection, a vision LLM reading each slide, a
  time-aligned timeline, then chapters and study notes with timestamps
  ([below](#processing-a-lecture)).
- Search ([below](#search)): each processed lecture is indexed in Qdrant with dense vectors
  (Qwen3-Embedding-0.6B) and BM25, and `GET /v1/search` runs dense, BM25, hybrid or reranked
  search. A golden Q&A set for Lecture 10 and a retrieval eval score each mode.
- Q&A ([below](#questions-and-answers)): ask about a lecture and get a streamed answer that
  cites the moments it comes from as `[mm:ss]`, each citation checked against what was
  retrieved. Follow-ups are rewritten to stand alone before searching. Threads, answers (with
  sources, tokens and time to first token) and thumbs up/down feedback are stored.
- Courses ([below](#courses)): group lectures, then search and ask across all of them, with
  citations that open the right lecture at the cited moment.
- A single-call Gemini baseline that summarises a lecture video and records tokens, cost and
  timings ([below](#gemini-baseline)).
- Direct-to-storage uploads: the API creates a lecture and hands out a presigned URL, the client
  uploads the file to storage, and the API confirms it.
- Postgres with Alembic migrations, SeaweedFS as local S3, Temporal, Qdrant, and Text Embeddings
  Inference servers for the embedding model and reranker, all in Docker Compose.
- Unit tests, plus integration tests that start real Postgres, SeaweedFS, Temporal and Qdrant
  with testcontainers and drive the whole flow through the API.
- CI: lint, type-check, tests, the web app's checks and build, dependency audits (Python and
  npm), image builds. Actions are pinned to commit SHAs.

## Getting started

Develop inside WSL2 (Ubuntu). Keep the repo on the Linux filesystem, e.g. `~/code/lecture-summariser`,
not under `/mnt/c/...`: cross-filesystem access is slow and breaks file watching.

You need:

- **Docker Desktop** with WSL integration turned on for Ubuntu (Settings → Resources → WSL integration)
- **uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **make**: `sudo apt install -y make`
- **Node 24** (only to work on the web app outside Docker), with pnpm through `corepack enable`

Then:

```bash
make install   # Python deps (uv picks Python 3.12) and git hooks
make up        # Postgres, SeaweedFS, Temporal, Qdrant and the embedding server
make migrate   # create the tables
make api       # API with auto-reload on http://localhost:8000 (docs at /docs)
```

No `.env` is needed: the defaults match the Compose stack. See [.env.example](.env.example) for
what can be changed. The first `make up` downloads the embedding model (1.1 GB) into a Docker
volume, and the first `make gpu-worker` the reranker (2.1 GB) and its GPU image, which takes a
few minutes.

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

## Web app

`make app` builds and runs the API, the CPU worker and the web app in Docker; add
`make gpu-worker` for speech recognition and the reranker. Then open http://localhost:3000:

- **Library**: upload a lecture (straight to storage, with a progress bar), optionally into a
  course. Processing starts on its own, and the page switches to the lecture. Courses are
  created and listed here too.
- **Course page**: its lectures, and tabs to ask or search across all of them. A citation or
  result opens the lecture it points into, playing from there.
- **Lecture page**: live processing progress, then the video with a slide strip that follows
  the slide on screen, chapters, and tabs for a transcript that highlights and scrolls with
  playback, the notes (formulas rendered with KaTeX), a quiz, search, and an Ask tab: a chat
  whose answers stream in, with citations that play the video from where they point. Every
  timestamp and search result plays the video from there. The header shows the lecture's
  course and moves it to another.

For hot reload while working on it, run `make web` (Node 24) alongside `make up`, `make api` and
the workers. The web app calls the API from the browser: its address is baked in at build time
(`NEXT_PUBLIC_API_URL`, default http://localhost:8000), and the API allows the web origin
(`CORS_ORIGINS`). After changing the API, `make openapi` regenerates the typed client; CI fails
if it's out of date.

## Processing a lecture

Processing needs the speech model in `data/models/faster-whisper-large-v3-turbo` (see
[Data and licensing](#data-and-licensing)), `GEMINI_API_KEY` in `.env`, and an NVIDIA GPU for
speech recognition.

### Through the API

Start the stack, with the API and CPU worker in Docker and the GPU worker for speech:

```bash
make up && make app && make gpu-worker
```

Then upload a lecture as in [Try the upload flow](#try-the-upload-flow), and:

```bash
curl -s -X POST "localhost:8000/v1/lectures/$ID/process"      # start (asking again returns the same run)
curl -sN "localhost:8000/v1/lectures/$ID/events"               # progress, streamed until it finishes
curl -s "localhost:8000/v1/lectures/$ID/notes"                 # also /transcript, /slides, /timeline, /runs
```

Watch the workflow in the Temporal UI at http://localhost:8233. The API docs at
http://localhost:8000/docs list every endpoint.

### On a local file

`make process` runs the same stages in order on a video file, with no API, database or Temporal,
and writes `notes.md` and `result.json` to `data/pipeline-runs/<video>/<time>/`:

```bash
make process video=data/lectures/MIT6_0001F16_Lecture_10_300k.mp4 title="Understanding Program Efficiency, Part 1"
```

Both paths cache every stage, so running again only redoes what changed: edit a prompt in
`prompts/pipeline/` and only the LLM stages re-run. The notes use the same format as the Gemini
baseline. Without a GPU, `uv sync --extra asr` (pipeline package) and `uv run lecture-process ...`
run speech recognition on the CPU.

How it works, and its known limitations, are in [docs/architecture.md](docs/architecture.md).

## Search

Processing indexes each lecture for search ([how it works](docs/architecture.md#search-phase-3a)).
Search one lecture, or leave out `lecture_id` to search them all:

```bash
curl -s "localhost:8000/v1/search?q=why+are+constants+ignored&lecture_id=$ID"              # SEARCH_MODE
curl -s "localhost:8000/v1/search?q=why+are+constants+ignored&lecture_id=$ID&mode=hybrid"  # no reranker
```

Use `course_id=...` instead of `lecture_id` to search a course's lectures.

`mode` is `dense`, `bm25`, `hybrid` or `rerank`. Without it, the API uses `SEARCH_MODE`: `rerank`
in Docker, where the reranker runs on the GPU (`make gpu-worker` starts it), and `hybrid` for
an API run on the host. Each hit has the segment's times, slide title, chapter and transcript.

To score search, process MIT 6.0001 Lecture 10 (the eval finds it by the video's hash), then run
`make eval-retrieval`. It asks the golden questions in
[evals/datasets/golden-qa](evals/datasets/golden-qa/mit-6.0001-lecture-10.json) in each mode,
prints Recall@5, MRR@10, nDCG@10 and latency, and saves every question's hits to
`data/evals/retrieval/`. The questions were drafted from the official captions and still need
checking by hand against the video.

## Questions and answers

Ask about a processed lecture (the API needs `GEMINI_API_KEY`, like processing):

```bash
curl -sN -X POST "localhost:8000/v1/lectures/$ID/ask" -H 'Content-Type: application/json' \
  -d '{"question": "Why do we ignore constants in big O notation?"}'
```

The answer streams as server-sent events: `start` (the thread and the saved question),
`sources` (the segments it draws on), `delta`s of text, then `done` with the saved answer and its
checked citations, or `error` with the reason. Send `thread_id` from `start` to ask a
follow-up. `GET /v1/lectures/$ID/threads` and `GET /v1/threads/{id}` read conversations back,
and `POST /v1/feedback` rates an answer:

```bash
curl -s -X POST localhost:8000/v1/feedback -H 'Content-Type: application/json' \
  -d '{"message_id": "<answer id>", "rating": "down", "reason": "Missed the second example"}'
```

An answer uses only the lecture: asked about merge sort, Lecture 10's answer is that the lecture
doesn't seem to cover it. How answers are built and checked is in
[docs/architecture.md](docs/architecture.md#qa-phase-3b). On the free tier, the same caveat
applies as for processing: public lectures only.

## Courses

```bash
COURSE=$(curl -s -X POST localhost:8000/v1/courses -H 'Content-Type: application/json' \
  -d '{"title": "MIT 6.0001 Fall 2016"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -X PATCH "localhost:8000/v1/lectures/$ID" -H 'Content-Type: application/json' \
  -d "{\"course_id\": \"$COURSE\"}"                                   # or pass course_id when creating it
curl -sN -X POST "localhost:8000/v1/courses/$COURSE/ask" -H 'Content-Type: application/json' \
  -d '{"question": "Why was nothing shown on the console in the print exercise?"}'
```

A course answer names each lecture with a label and cites it as `[L2 12:34]`; each source in the
`sources` event says which lecture L2 is. `GET /v1/courses` lists courses, `GET /v1/courses/{id}`
shows one with its lectures, and `GET /v1/courses/{id}/threads` its conversations. Deleting a
course keeps its lectures. Lectures processed before search existed need processing once more
to be indexed; everything but the embedding and indexing is cached.

## Gemini baseline

`gemini-baseline` sends a whole lecture video to Gemini in one call and asks for the study notes
the pipeline will produce: a TL;DR, chapters, key concepts, formulas and a quiz, each with a
timestamp. It sets the bar the pipeline has to beat, and answers "why not just use Gemini?" with
numbers.

1. Get an API key at https://aistudio.google.com/apikey and add `GEMINI_API_KEY=...` to `.env`.
2. Put lecture videos in `data/lectures/` (git ignores `data/`). On the free tier Google may use
   what you send to improve its products, so stick to public, openly licensed lectures.
3. Optionally install ffmpeg (`sudo apt install -y ffmpeg`) so the script can read the video's
   length itself. Otherwise it uses the length Gemini reports.
4. Run it:

```bash
uv run gemini-baseline data/lectures/lecture-15.mp4 --title "Dynamic programming"
```

Each run writes a folder under `data/baselines/<video>/`:

- `notes.md`: the notes, for reading.
- `result.json`: the notes in the shared format (`lecture_core.notes.StudyNotes`, times in
  seconds), plus the model, prompt and video hashes, tokens by modality, cost, timings, and
  checks: timestamps outside the video, chapter gaps and overlaps, and how much of the video the
  chapters cover.
- `response.json`: the model's raw output.

| Option | Default | Notes |
|---|---|---|
| `--model` | `gemini-3.8-flash` | Prices for common models are built in ([pricing.py](evals/src/lecture_evals/pricing.py)). For others, pass `--input-price` and `--output-price` (USD per million tokens). |
| `--processing` | `static` | `static` puts every frame in context. `agentic` lets the model choose where to look, which uses fewer tokens. Worth running both. |
| `--fps` | `1` | Frames per second, `static` only. Slides change slowly, so try `0.5`. |
| `--media-resolution` | `low` | About 100 tokens per second of video. `high` is about 300 and reads small text better. |
| `--prompt` | `prompts/baseline-gemini/v1.md` | To try a change, copy it to `v2.md`. Each run records which prompt it used. |

At low resolution a one-hour lecture is roughly 360k input tokens, so a run on
`gemini-3.8-flash` costs about $0.30. That model's prices double on 1 January 2027. Uploads are
reused for 48 hours, so repeat runs on the same video skip the upload.

## Results so far

One lecture so far: MIT 6.0001 Lecture 10 (51 min).

### Study notes

Scored the same way for both producers.

| | Gemini, one call | Pipeline |
|---|---|---|
| Notes | 8 chapters, 8 concepts, 3 formulas, 9 quiz | 5 chapters, 13 concepts, 12 formulas, 9 quiz |
| Concept citations within 10 s of where the captions say the term | 6 of 8, median 1.7 s | 11 of 13, median 0.5 s |
| API cost | $0.21 (`gemini-3.8-flash`) | about $0.04 (`gemini-3.5-flash-lite`, paid-tier prices) |
| Time | 135 s | 119 s through the API and workers (speech and slides in parallel), 15 s when only the notes change |
| Speech recognition | | 65 s on an RTX 3060 Laptop (6 GB): 47× real time, 2.3 GB VRAM peak |

Caveats: one lecture, two different Gemini models, and a rough citation measure (caption text
matching). Whether the notes are faithful to the lecture isn't scored yet; that's the Phase 4
LLM judge.

### Search

The 33 golden questions against the lecture's 47 segments (`make eval-retrieval`). A hit counts
when its segment overlaps the answer by at least 3 seconds. Latency is through the API, with the
reranker on the GPU.

| Mode | Recall@5 | MRR@10 | nDCG@10 | Median latency |
|---|---|---|---|---|
| dense | 0.97 | 0.84 | 0.82 | 0.4 s |
| BM25 | 0.91 | 0.76 | 0.74 | 8 ms |
| hybrid (RRF) | 0.97 | 0.84 | 0.84 | 0.4 s |
| hybrid + rerank | **1.00** | **0.89** | **0.86** | 0.8 s |

The reranker is the only step that clearly helps: it puts the answer first for 26 questions,
against 24 for hybrid. On the CPU it took 77 seconds a query, which is why it runs on the GPU.
With a single lecture there's little for hybrid to beat dense on (one question is 0.03 of
Recall@5); more lectures, and course-wide search, should separate them. Embedding a lecture
takes 5 minutes on the CPU, so a fresh run now takes about 6 minutes, up from 2. More in
[ADR 0005](docs/adr/0005-qdrant-for-hybrid-search.md).

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start or stop Postgres, SeaweedFS, Temporal, Qdrant and the embedding server (`make reset` also deletes their data) |
| `make app` | Build and run the API, the web app and the CPU worker in Docker |
| `make gpu-worker` | Build and run the GPU services in Docker: speech recognition and the reranker |
| `make worker` | Run a CPU worker on the host instead |
| `make api` | Run the API on the host with auto-reload |
| `make web` | Run the web app on the host with hot reload (Node 24) |
| `make openapi` | Regenerate the web app's typed API client after an API change |
| `make eval-retrieval` | Score search on the golden Q&A set (needs the stack running and Lecture 10 processed) |
| `make migrate` | Apply migrations |
| `make revision m="add chapters"` | Generate a migration after changing `packages/core/src/lecture_core/models.py` |
| `make test` / `make test-unit` | All tests / unit tests only |
| `make lint` / `make fmt` / `make typecheck` | Ruff check / Ruff fix and format / mypy (strict) |
| `make audit` | pip-audit on the locked dependencies |
| `make check` | Lint, type-check and test, like CI |

## Layout

```
apps/api/                  FastAPI service (routes, schemas, dependencies)
apps/web/                  Next.js web app (library, lecture page); typed client from openapi.json
packages/core/             settings, SQLAlchemy models, Alembic migrations, S3 client
packages/perception/       PyAV media reading, slide detection, speech recognition
packages/llm/              Pydantic AI agents: read slides, chapters, notes
packages/pipeline/         stages, stage cache, timeline, local runner, Temporal workflow and workers
packages/rag/              chunks, encoders (TEI, BM25), Qdrant index, hybrid search
evals/                     Gemini baseline, retrieval eval, golden Q&A sets (evals/datasets/)
prompts/                   versioned prompts (pipeline, Q&A and baseline)
data/                      lecture videos and run outputs (not in git)
tests/unit/                fast tests, no Docker
tests/integration/         real Postgres, SeaweedFS, Temporal and Qdrant via testcontainers
infra/compose.yaml         local stack (`app` profile adds the API, web app and CPU worker)
infra/docker/              Dockerfiles: API, worker (CPU and GPU variants)
infra/seaweedfs/s3.json    dev-only S3 credentials
docs/                      blueprint, architecture, ADRs
```

## Tests

`make test` runs everything. Integration tests are marked `integration` and start their own
containers, so they need Docker but not `make up`. If Docker isn't running they're skipped
locally. In CI they must run and fail instead.

One integration test runs `alembic check`: it fails if a model changed without a migration.

## Decisions

Architecture decisions are recorded in [docs/adr](docs/adr/README.md). The build so far differs
from the blueprint in these places:

- **The search index doesn't store course ids.** A course search filters by the ids of the
  course's lectures, so a lecture can move between courses without re-indexing
  ([architecture](docs/architecture.md#courses-phase-3)).
- **Temporal and Qdrant came into Compose when first used**, not in Phase 1: Temporal in Phase 2
  ([ADR 0002](docs/adr/0002-temporal-for-orchestration.md)), Qdrant in Phase 3
  ([ADR 0005](docs/adr/0005-qdrant-for-hybrid-search.md)).
- **The reranker runs on the GPU locally**, not the CPU: on a laptop CPU it took 77 seconds to
  rerank one query's passages. It needs 1.3 GB of VRAM beside speech recognition. The embedding
  model stays on the CPU. Without a GPU, set `SEARCH_MODE=hybrid`
  ([ADR 0005](docs/adr/0005-qdrant-for-hybrid-search.md)).
- **Uploads are a single presigned PUT** (up to 5 GiB), not resumable multipart through Uppy.
  Worth adding when uploads get large or flaky.
- **The player streams the uploaded MP4 directly** (a presigned URL with range requests) rather
  than HLS renditions. Transcoding to HLS comes back if other formats or adaptive bitrate are
  needed.
- **The web app uses Tailwind without shadcn/ui**, which can come in when there are more
  components to share.
- **CI builds the images (API, CPU worker, web) but doesn't scan or push them.** That comes with
  deployment in Phase 6.
- **No update bot (Dependabot or Renovate) opening PRs.** Dependencies are updated by hand with
  `uv lock --upgrade`, and CI audits the lockfile on every push. GitHub's Dependabot security
  alerts (Settings → Code security) still flag vulnerable packages without committing anything.

## Roadmap

- [x] **Phase 1, foundation**: workspace, Compose, API skeleton, migrations, stage cache, CI, first ADRs
- [ ] **Before Phase 2** (a few evenings):
  - [ ] Pick 3 slide-based MIT OCW lectures that have captions and slide PDFs (1 so far:
        6.0001 Lecture 10). Many OCW lectures
        are chalkboard-only, which slide detection won't handle.
  - [x] Run the single-call Gemini baseline on them to set the bar the pipeline has to beat
        (static mode on Lecture 10; agentic mode still to do).
  - [x] Check faster-whisper int8 on the GPU inside Docker, and note peak VRAM.
  - [x] Draft 30 golden Q&A questions, so Phase 3 retrieval choices can be measured (33 for
        Lecture 10, plus 3 it doesn't answer; still to check by hand).
- [x] **Phase 2, vertical slice**
  - [x] 2a: pipeline stages (ASR, slide detection, vision LLM, timeline, chapters, notes), stage
        cache, GPU worker image, local runner
  - [x] 2b: Temporal workers, process/progress/results API, new tables
  - [x] 2c: web app (upload, live progress, lecture page with player, synced transcript, slides,
        chapters, notes, quiz)
- [x] **Phase 3, RAG Q&A**
  - [x] 3a: chunks, embeddings, Qdrant index, hybrid search and reranking, search API and tab,
        golden Q&A set and retrieval eval
  - [x] 3b: streamed answers with checked `[mm:ss]` citations, chat panel, threads and feedback
  - [x] Courses: search and answers across a course's lectures
- [ ] **Phase 4, evals and observability**: eval suites, Langfuse, OpenTelemetry, CI eval gate
- [ ] **Phase 5, CV and optimisation**: YOLO26 fine-tune, OCR-vs-VLM routing, ONNX/TensorRT/int8
- [ ] **Phase 6, ship**: auth, quotas, Terraform and Modal deploy, results write-up

## Data and licensing

The speech model isn't in git. Download it, pinned to the revision the code expects, with:

```bash
mkdir -p data/models/faster-whisper-large-v3-turbo && cd data/models/faster-whisper-large-v3-turbo && for f in config.json preprocessor_config.json tokenizer.json vocabulary.json model.bin; do curl -fLO "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf/$f"; done
```

`model.bin` should have SHA-256 `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`
(1.62 GB, MIT licence).

The embedding model (Qwen3-Embedding-0.6B) and the reranker (bge-reranker-v2-m3), both
Apache-2.0, download into the `tei-data` Docker volume the first time their containers start,
pinned to the revisions in `infra/compose.yaml`. FastEmbed downloads its BM25 files
(`Qdrant/bm25`, a few kB) on first use.

Lecture videos never go in git (`.gitignore` blocks common video formats). MIT OCW material is
CC BY-NC-SA 4.0: record the licence and attribution on each lecture (the API has fields for both),
and keep the demo non-commercial. The golden Q&A sets are questions written for this project about
those lectures, with the lecture's attribution in each file. The repo itself has no licence yet. Choose one with the
YOLO26/RF-DETR decision in Phase 5, since YOLO26 is AGPL-3.0.
