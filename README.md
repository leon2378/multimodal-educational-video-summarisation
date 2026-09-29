# Lecture Summariser

Turns lecture videos into timestamp-grounded study notes and a Q&A chat whose answers cite the
moment in the lecture they come from.

**Status: Phases 1 to 3 of 6 done, Phase 4 done but for the CI gate, Phase 5 under way (OCR routing done, a frame detector trained): upload a lecture in the browser, watch it process, then study it with a synced transcript, slides, chapters and notes, search it, and ask questions whose answers cite the moments they come from, about one lecture or a whole course. Eval suites score each part and gate regressions, and traces, metrics and logs show where the time and money go.** The full design is in [docs/blueprint.md](docs/blueprint.md).
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
- The processing pipeline: speech recognition, slide detection, OCR on every slide with a vision
  LLM for the slides OCR can't handle (figures, annotations), a time-aligned timeline, then
  chapters and study notes with timestamps
  ([below](#processing-a-lecture)).
- Search ([below](#search)): each processed lecture is indexed in Qdrant with dense vectors
  (Qwen3-Embedding-0.6B) and BM25, and `GET /v1/search` runs dense, BM25, hybrid or reranked
  search.
- Q&A ([below](#questions-and-answers)): ask about a lecture and get a streamed answer that
  cites the moments it comes from as `[mm:ss]`, each citation checked against what was
  retrieved. Follow-ups are rewritten to stand alone before searching. Threads, answers (with
  sources, tokens and time to first token) and thumbs up/down feedback are stored.
- Courses ([below](#courses)): group lectures, then search and ask across all of them, with
  citations that open the right lecture at the cited moment.
- Evals ([below](#evals)): suites for speech recognition (word error rate against the
  captions), notes (concept citations), search (Recall@5, MRR, nDCG) and answers (an LLM judge
  for correctness and faithfulness, plus citation accuracy), recorded in Postgres and gated by
  thresholds.
- Observability ([below](#observability)): OpenTelemetry traces, metrics and logs from the API
- A frame detector ([below](#frame-detector)): RF-DETR fine-tuned to find slides, people,
  figures and annotations in video frames, on frames labelled automatically from the lectures'
  slide PDFs. Trained and scored; not in the pipeline yet.
  and workers into Grafana. One trace follows a request through the workflow's activities to
  each LLM call. A dashboard tracks the blueprint's targets, and every answer and pipeline run
  records its cost. The LLM calls can also go to Langfuse.
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

`make eval-retrieval` scores search on the golden questions ([Evals](#evals)).

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

## Evals

The eval suites score the running stack the way a user would reach it, through the API, on
MIT 6.0001 Lecture 10 (process it first; the suites find it by the video's hash):

| Suite | What it measures | Dataset |
|---|---|---|
| `retrieval` | Recall@5, MRR@10 and nDCG@10 for each search mode | 33 golden questions with answer spans |
| `answers` | correctness and faithfulness (an LLM judge), citations inside the retrieved passages and near the answer, declining what the lecture doesn't cover, time to first token, tokens | the same questions, plus 3 the lecture doesn't answer |
| `asr` | word error rate against the human captions, recall of 34 technical terms, real-time factor | the captions |
| `notes` | concepts cited within 10 s of where the captions say the term, timestamp and chapter checks | the captions |
| `slides` | how much of each slide's text the readings recover (word precision, recall, F1), by reader, and slides without a title | the lecture's slide PDF |

```bash
make eval                                   # every suite; exits 1 if a metric is past its threshold
uv run lecture-eval --suites asr,notes      # some of them
uv run lecture-eval --suites notes --notes-file data/baselines/<video>/<run>/result.json   # score the Gemini baseline's notes
```

Each suite prints a summary, writes the details (every question, answer and verdict) to
`data/evals/<suite>/`, and records an `eval_runs` row: the suite, the dataset's hash, the git
commit, the models and settings, and the metrics. `evals/thresholds.json` holds the bounds the
gate checks, set a little below today's scores.

The captions and slide PDF aren't in git: they're the lecture's own material (CC BY-NC-SA).
Each dataset file names the file to put in `data/lectures/`, where to get it, and its SHA-256. The answer judge uses
`LLM_MODEL` (or `--judge-model`), the same Gemini model that answers, and it isn't calibrated
against hand grades yet, so treat its scores as a trend. The golden questions were drafted from
the captions and still need checking by hand against the video.

## Observability

Off until you point the services at a collector:

```bash
make observability                                    # Grafana, Tempo, Prometheus and Loki in one container
echo 'OTEL_ENDPOINT=http://otel-lgtm:4318' >> .env
make app && make gpu-worker                           # recreate the services with the setting
```

Processes on the host (`make api`, `make worker`) use `OTEL_ENDPOINT=http://localhost:4318`.
Grafana is on http://localhost:3001, and the **Lecture Summariser** dashboard has:

- **Targets** from the blueprint: Q&A time to first token (p95), LLM cost per lecture-hour,
  processing minutes per lecture-hour, and the share of answers rated helpful.
- **Questions and answers**: time to first token and answer time (p50 and p95), answers by
  outcome, ratings.
- **LLM tokens and cost**, by purpose (each pipeline stage, and Q&A) and model.
- **Pipeline**: time per stage when it isn't cached, cache hits, how long activities wait on
  each task queue, slots in use, GPU memory, and recent runs with their cost and GPU seconds.
- **API** request rates and latency, every **eval** run over time (from `eval_runs`), and recent
  **traces** and **warnings**.

Each request is one trace in Tempo; click one in the dashboard, or use Explore. Processing a
lecture shows the API request, then the workflow and each activity on the CPU and GPU workers,
with their SQL and HTTP calls. A question shows the query embedding, the Qdrant search and the
reranking, the SQL, and the Gemini call with its prompt, reply and tokens. Log lines in Loki
carry their trace id.

Costs use paid-tier prices ([pricing.py](packages/llm/src/lecture_llm/pricing.py)) even on
Gemini's free tier, so they show what running this would cost. Answers return `cost_usd`, and
each pipeline run stores what the LLM calls behind its notes cost (`pipeline_runs.llm_usage`).

To send the LLM calls to Langfuse as well, add `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
(plus `LANGFUSE_HOST` for the US region or a self-hosted instance) to `.env`. Only the model calls
go there, not SQL or HTTP spans. They include the prompts and replies, so lecture text and
questions leave the machine: fine for openly licensed lectures, not for private ones. On
Langfuse Cloud each answer shows up as an agent run and a model call, with the model, prompt,
reply, tokens, cost and latency. Why this
setup: [ADR 0006](docs/adr/0006-opentelemetry-to-grafana-and-langfuse.md).

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
| Notes | 8 chapters, 8 concepts, 3 formulas, 9 quiz | 5 chapters, 14 concepts, 7 formulas, 9 quiz |
| Concept citations within 10 s of where the captions say the term | 3 of 4, median 2.2 s | 9 of 10, median 0.5 s |
| Concepts whose term is never said as written (slide titles), so can't be checked | 4 | 4 |
| API cost | $0.21 (`gemini-3.8-flash`) | about $0.04 (`gemini-3.5-flash-lite`, paid-tier prices) |
| Time | 135 s | 119 s through the API and workers (speech and slides in parallel), 15 s when only the notes change |
| Speech recognition | | WER 3.0% against the human captions, 99.1% of the technical terms; 65 to 74 s on an RTX 3060 Laptop (6 GB), about 45× real time, 2.3 GB VRAM peak |

Scored by `lecture-eval --suites notes,asr`. Caveats: one lecture, two different Gemini models,
and a citation measure that can only check terms said as written.

### Answers

The 36 golden questions asked through the API (`make eval`), answered by
`gemini-3.5-flash-lite` and graded by the same model. The judge isn't calibrated yet; checking
9 of the answers by hand agreed with it.

| Correct | Faithful to the passages | Citations inside the passages | Citing near the answer | Declined when not covered | First token, p50 / p95 |
|---|---|---|---|---|---|
| 0.98 | 1.00 | 0.99 | 0.97 | 3 of 3 | 2.1 s / 8.1 s |

The one answer marked partly right: asked how many operations the summing loop takes, it gave
the 3 per iteration but not the total, 3x + 2. First-token times vary with Gemini's free tier:
the p95 was 19 s on an earlier run. An answer costs about $0.0015 at paid-tier prices: around
4,500 input tokens, mostly the six passages, and 70 output tokens on average.

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

### Slide reading

The tables above were measured with the vision LLM reading every slide. Since Phase 5a, OCR
reads every slide and only the slides OCR can't handle go to the vision LLM (`SLIDE_READER`).
Lecture 10 read all three ways, everything else the same; slide text is scored against the
lecture's slide PDF by word overlap (`lecture-eval --suites slides`), and the notes and answers
are regenerated from each reading:

| | Vision LLM, every slide | Routed (default) | OCR only |
|---|---|---|---|
| Slides the vision LLM reads | 24 | 11 | 0 |
| Slide text against the PDF, word F1 | 0.80 | 0.84 | 0.82 |
| Slides without a title | 9 | 0 | 0 |
| Formulas in the notes | 7 | 8 | 3 |
| Search with reranking, Recall@5 / MRR@10 | 1.00 / 0.89 | 1.00 / 0.92 | 1.00 / 0.94 |
| Answers correct / faithful / citing near the answer | 0.98 / 1.00 / 0.97 | 1.00 / 1.00 / 1.00 | 0.95 / 0.97 / 0.97 |
| Reading slides: LLM tokens in / out | 26,729 / 3,551 | 12,471 / 2,236 | none |
| Reading slides: cost at paid-tier prices | $0.017 | $0.009 | $0 |
| All of the lecture's LLM calls | $0.041 | $0.035 | $0.024 |

OCR (RapidOCR's PP-OCRv6 models on the CPU, 12 s for the 24 slides) reads plain text as well
as the vision LLM, tables better, and never skips a title: the vision LLM left 9 empty, a whole
batch of 8 among them. What OCR can't do is describe a plot or a diagram, write LaTeX, or keep
the lecturer's annotations apart from the slide text. With OCR alone the notes kept 3 of the
formulas, and one answer went wrong: asked for the three ways of measuring efficiency, it missed
"order of growth", which OCR had read but between the lines of an annotation written across the
slide. Routing sends slides with figures, angled text or doubtful OCR to the vision LLM, which
keeps those, and halves the cost of reading slides. The differences in search and answers are
within what one lecture and one judge can separate.

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start or stop Postgres, SeaweedFS, Temporal, Qdrant and the embedding server (`make reset` also deletes their data) |
| `make app` | Build and run the API, the web app and the CPU worker in Docker |
| `make gpu-worker` | Build and run the GPU services in Docker: speech recognition and the reranker |
| `make observability` | Start Grafana with traces, metrics and logs on http://localhost:3001 (set `OTEL_ENDPOINT` to send to it) |
| `make worker` | Run a CPU worker on the host instead |
| `make api` | Run the API on the host with auto-reload |
| `make web` | Run the web app on the host with hot reload (Node 24) |
| `make openapi` | Regenerate the web app's typed API client after an API change |
| `make eval` | Run every eval suite against the running stack, record it, and check the thresholds |
| `make eval-retrieval` | Score search only |
| `make migrate` | Apply migrations |
| `make revision m="add chapters"` | Generate a migration after changing `packages/core/src/lecture_core/models.py` |
| `make test` / `make test-unit` | All tests / unit tests only |
| `make lint` / `make fmt` / `make typecheck` | Ruff check / Ruff fix and format / mypy (strict) |
### Frame detector

RF-DETR Nano ([ADR 0007](docs/adr/0007-rf-detr-for-the-frame-detector.md)), fine-tuned on
frames from MIT 6.0001 Lectures 10 and 11 and scored on Lecture 12, which it never saw. No box
was drawn by hand (`make detector-data`, `make detector-train`): each slide frame is matched to
its page of the lecture's slide PDF and aligned to it by OCR'd text lines (median error under 2
pixels); the vision LLM boxes each page's figures and annotations once (125 boxes on 117 pages),
and those boxes carry onto every frame that shows the page; a COCO-trained RF-DETR finds people.
Frames the labeller can't be sure of (a slide playing a video, a code demo) are left out.

| | Train (Lectures 10, 11) | Valid | Test (Lecture 12) |
|---|---|---|---|
| Frames | 324 | 96 | 177 |
| Boxes: slide / person / figure / annotation | 144 / 176 / 67 / 47 | 53 / 43 / 14 / 11 | 78 / 102 / 25 / 8 |

On the test lecture, against its automatic labels (mAP50:95 0.66, mAP50 0.73):

| Class | AP50:95 | Precision | Recall |
|---|---|---|---|
| slide | 1.00 | 0.96 | 1.00 |
| person | 0.99 | 1.00 | 1.00 |
| annotation | 0.50 | 1.00 | 0.63 |
| figure | 0.15 | 0.32 | 0.36 |

Routing, deciding which slides go to the vision LLM, on the test lecture's 78 slide frames (24
with a figure or annotation by the labels):

| | Slides routed | Precision | Recall | F1 |
|---|---|---|---|---|
| The 5a rule: ink outside text, angled lines, OCR confidence | 27 | 0.74 | 0.83 | 0.78 |
| The detector, confidence 0.5 | 13 | 0.85 | 0.46 | 0.59 |

Finding slides and people is solved at this scale, and the detector also recognises a slide
playing a video, which the brightness test takes for a camera shot. Figures don't generalise
from two lectures: Lecture 12's photos and sorting diagrams look nothing like the plots and
memory diagrams of 10 and 11, and the LLM's boxes aren't consistent (highlighted code sometimes
counts as a figure). Training at 512 px instead of 384 didn't help (figure AP 0.03, mAP 0.63).
So the 5a rule keeps routing slides, and the detector stays out of the pipeline until more
lectures are labelled. Every score here is against labels a model made, not checked by hand.

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
evals/                     eval suites (lecture-eval), datasets, thresholds, Gemini baseline
| `make detector-data` | Label frames for the frame detector from the lectures' videos and slide PDFs (installs PyTorch, 2 GB, the first time) |
| `make detector-train` | Fine-tune the frame detector on the GPU and score the held-out lecture |
prompts/                   versioned prompts (pipeline, Q&A and baseline)
data/                      lecture videos and run outputs (not in git)
tests/unit/                fast tests, no Docker
tests/integration/         real Postgres, SeaweedFS, Temporal and Qdrant via testcontainers
infra/compose.yaml         local stack (`app` profile adds the API, web app and CPU worker)
infra/grafana/             Grafana datasource and dashboard (JSON), loaded by `make observability`
infra/docker/              Dockerfiles: API, worker (CPU and GPU variants)
infra/seaweedfs/s3.json    dev-only S3 credentials
docs/                      blueprint, architecture, ADRs
```

## Tests

`make test` runs everything. Integration tests are marked `integration` and start their own
containers, so they need Docker but not `make up`. If Docker isn't running they're skipped
locally. In CI they must run and fail instead.

One integration test runs `alembic check`: it fails if a model changed without a migration.
ml/detector/               frame detector: labels from slide PDFs, dataset, RF-DETR training

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
- **Slides are routed to the vision LLM by simple image measures until the detector exists.**
  The blueprint routes by the detector's figure, table and equation boxes; until Phase 5b, ink
  outside the OCR'd lines, lines at an angle and OCR confidence stand in
  ([architecture](docs/architecture.md#ocr-and-routing-phase-5a)). OCR uses the PP-OCRv6 models
  that come with RapidOCR rather than the PP-OCRv5 the blueprint names: newer, and nothing more
  to download.
- **Eval runs are recorded in Postgres (`eval_runs`) and charted in Grafana**, not MLflow.
  MLflow comes in with detector training in Phase 5.
- **Langfuse only receives LLM calls, and only when its keys are set.** Prompts are versioned in
  git and eval datasets live in `evals/`, so Langfuse isn't where those are kept. No Sentry yet:
  errors are logged to Loki with their trace ids
  ([ADR 0006](docs/adr/0006-opentelemetry-to-grafana-and-langfuse.md)).
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
- [ ] **Phase 4, evals and observability**
  - [x] 4a: eval suites (search, answers with an LLM judge, speech recognition, notes),
        `eval_runs`, thresholds and a local gate (`make eval`)
  - [x] 4b: OpenTelemetry traces, metrics and logs, a Grafana dashboard, LLM tracing (Langfuse
        optional), cost per answer and per run
  - [ ] 4c: the eval gate in CI
- [ ] **Phase 5, CV and optimisation**
  - [x] 5a: OCR on every slide (RapidOCR), the vision LLM only for slides with figures,
        annotations or doubtful OCR, and a slides eval against the slide PDF
  - [ ] 5b: a frame detector (RF-DETR, [ADR 0007](docs/adr/0007-rf-detr-for-the-frame-detector.md))
        for slides, people, figures and annotations, on frames labelled automatically from the
        slide PDFs: trained and scored on 3 lectures; into the pipeline once more lectures
        make it route better than the 5a rule
  - [ ] 5c: ONNX/TensorRT/int8 benchmarks, before and after
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
