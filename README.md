# Lecture Summariser

Turns lecture videos into timestamp-grounded study notes and a Q&A chat whose answers cite the
moment in the lecture they come from.

**Status: Phases 1 to 4 of 6 done, Phase 5 done but for the detector in the pipeline (OCR routing, a trained frame detector, speed benchmarks), Phase 6 under way (sign-in and quotas done; the on-demand cloud demo built, its first cloud run next): upload a lecture in the browser, watch it process, then study it with a synced transcript, slides, chapters and notes, search it, and ask questions whose answers cite the moments they come from, about one lecture or a whole course. Eval suites score each part and gate regressions in CI, and traces, metrics and logs show where the time and money go.** The full design is in [docs/blueprint.md](docs/blueprint.md).
What exists today is described in [docs/architecture.md](docs/architecture.md).

## What works now

- A uv workspace: `apps/api` (FastAPI), `packages/core` (settings, models, storage, the timeline
  and study-notes formats), `packages/perception` (video, audio, slide detection, speech
  recognition), `packages/llm` (Pydantic AI agents for the pipeline and Q&A), `packages/pipeline` (stages, stage cache,
  local runner), `packages/rag` (chunking, embeddings, the search index, hybrid search) and
  `evals` (baselines, golden sets and scoring).
- A web app ([below](#web-app)): drag-and-drop upload, live processing progress, and a
  lecture page with the video, a transcript that follows playback, slides and what was read
  from them, chapters, notes, a quiz, search and a Q&A chat, every timestamp clickable. Ctrl+K
  searches the whole library.
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
  thresholds, in CI as well as locally.
- A frame detector ([below](#frame-detector)): RF-DETR fine-tuned to find slides, people,
  figures and annotations in video frames, on frames labelled automatically from the lectures'
  slide PDFs, from 12 lectures. Trained and scored; it routes slides worse than the 5a rule, so
  it isn't in the pipeline.
- Speed benchmarks ([below](#speed)): `lecture-bench` runs speech recognition, the embedding
  model and the frame detector each way they could run (CPU or GPU; fp32, fp16 or int8;
  PyTorch, ONNX Runtime or TensorRT) and scores every variant, so lost accuracy shows. The
  embedding model is 150 times faster on the GPU, so Compose now runs it there when there is one.
- Sign-in and quotas ([below](#sign-in-and-quotas)): Clerk sign-in in the web app, whose
  session tokens the API checks. Visitors read and search the public demo lectures; signed-in
  users ask questions and upload private lectures within daily quotas, under a daily ceiling on
  LLM spend. Off until configured.
- An on-demand demo ([below](#deploying-the-demo)): Terraform makes a Google Cloud VM that runs
  the whole stack behind HTTPS for a session, loads the public lectures from the stage cache,
  and is deleted after; uploads are transcribed on GPUs in Modal. Rehearsed locally; not yet
  run in the cloud.
- Observability ([below](#observability)): OpenTelemetry traces, metrics and logs from the API
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
  npm), image builds, and the eval gate on the whole stack. On a version tag, the images are
  scanned and pushed to GHCR. Actions are pinned to commit SHAs.

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

- **Library**: lectures (their first slide as the cover) and courses, filtered by title or
  status. Drop a video anywhere, or use Add lecture: it uploads straight to storage with
  progress, speed and time left (and can be cancelled), optionally into a course and with its
  licence and attribution. Processing starts on its own, and the page switches to the lecture.
- **Lecture page**: while it processes, each stage by phase, with timings and what came from
  the cache. Then the video with a chapter bar under it and a slide strip that follows the slide
  on screen, beside tabs for:
  - **Notes**: the summary, chapters (the one playing marked), key concepts and formulas
    (KaTeX), to copy or download as Markdown.
  - **Transcript**: highlights and scrolls with playback, headed by chapter; its search box
    searches the lecture.
  - **Slides**: each slide's text, formulas, code and figure description, and whether OCR or
    the vision model read it.
  - **Quiz**: reveal answers and mark what you knew; it remembers, per lecture.
  - **Ask**: a chat whose answers stream in (and can be stopped), with citations that play the
    video from where they point, suggested questions from the notes, and each answer's model,
    time and cost. Conversations can be switched between and deleted.

  Every timestamp plays the video from there. K, J and L (or the arrows) control playback. The
  header moves the lecture between courses, copies a link to the current moment, shows the
  processing history (stage times, LLM tokens and cost per run) and processes it again.
- **Course page**: its lectures, and tabs to ask or search across all of them. A citation or
  result opens the lecture it points into, playing from there. Lectures can be added to it
  directly, and the course deleted (its lectures stay).
- **Ctrl+K** (⌘K on a Mac) anywhere: go to a lecture or course by title, or search what was
  said and shown across every lecture.

With sign-in on, it signs in and up through Clerk's own windows, with an account button in
the header that also shows the questions and uploads left today. Signed out, the library shows
the public lectures and courses and search works; asking, uploading and making courses ask you
to sign in first. Your own private lectures carry a badge, and admins can make lectures and
courses public. A spent quota says when it resets, and a banner says when everyone's daily
budget has run out. With sign-in off (no Clerk key), none of this shows.

It has light and dark themes, following the system's by default, and works down to phone width.

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

## Sign-in and quotas

Sign-in is off until the API has an issuer to trust: locally, every request is one user who can
see and change everything, with no limits. Set `AUTH_ISSUER` to your Clerk instance's Frontend
API URL (Clerk dashboard, API keys) and the API checks Clerk's session tokens against Clerk's
published keys; it holds no Clerk secret ([ADR 0009](docs/adr/0009-clerk-sign-in-and-quotas.md)).

| | Without an account | Signed in | Admin (`ADMIN_USERS`) |
|---|---|---|---|
| Read and search the public lectures (the demo) | yes | yes | yes |
| Ask questions | no | 30 a day, 5 a minute | no limit |
| Upload and process lectures, private to you | no | 3 a day, up to 1 GB each | no limit |
| Make courses (private) | no | yes | yes, and public ones |
| Make a lecture public | no | no | yes |

On top of that, everyone together stops at $2 of LLM spend a day (at paid-tier prices): past it,
questions and processing wait for 00:00 UTC. A limit answers 429 with `Retry-After`, and
`GET /v1/me` says who the API takes the caller to be and what their quotas leave today.
Conversations are private to whoever had them. The limits are settings (see `.env.example`).
Lectures added before sign-in existed, or with it off, are public.

To turn it on, create a Clerk application and put in `.env`:

- `AUTH_ISSUER`: the instance's Frontend API URL, and `AUTH_AUTHORIZED_PARTIES`: the web app's
  origin, `["http://localhost:3000"]` locally. A token issued for another origin is refused,
  so a missing origin shows up as the web app's sign-in being rejected.
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` for the web app. The publishable
  key is built into it and public by design; the secret key stays on its server.
- `ADMIN_USERS`: your own Clerk user id (`user_...`), to see everything and publish lectures.

Then `make app` rebuilds the API and the web app and applies the migration.

## Deploying the demo

The demo runs on Google Cloud only while it's needed
([ADR 0010](docs/adr/0010-on-demand-demo-on-google-cloud-and-modal.md)). `make deploy` makes one
VM that runs the whole stack behind HTTPS and loads the public MIT lectures from the stage cache,
so nothing is transcribed or sent to an LLM again. About ten minutes later it answers at
`https://<its IP, with dashes>.sslip.io`, which needs no domain. `make destroy` deletes it. The
VM costs about $0.07 an hour while it exists; the bucket with the videos and the stage cache
stays.

- **Visitors** browse, search and play the demo lectures; signed-in users ask questions and
  upload their own, within the quotas ([above](#sign-in-and-quotas)).
- **Uploads** are transcribed on a GPU in Modal and indexed with the embedding model on another
  (`infra/modal/`). Questions use hybrid search on the VM's CPU, without the reranker.
- **Releases**: pushing a tag like `v0.6.0` builds the API, worker and web images, scans them for
  known vulnerabilities, and pushes them to GHCR. A deploy runs a release's images.
- **From GitHub**: the *deploy* workflow (Actions, Run workflow) does what `make deploy` and
  `make destroy` do, signed in to Google Cloud without a stored key.

Setting it up the first time (needs the Google Cloud CLI, Terraform and a Modal account):

1. `gcloud auth login` and `gcloud auth application-default login`, then copy
   `infra/terraform/cloud.tfvars.example` to `cloud.tfvars` and name your project in it. Set a
   budget alert on the project's billing account.
2. `make cloud-base`: switches on the APIs Terraform needs, makes Terraform's state bucket,
   then the bucket, secrets, network and deploy access.
3. Modal: `uv run modal token new`, then `make modal-model` once and `make modal`. Make a proxy
   auth token for the embedding server in Modal's dashboard.
4. Copy `infra/cloud.env.example` to `infra/cloud.env`, fill it in (Gemini, Clerk, Modal) and run
   `make cloud-secrets`.
5. With the local stack running, `make cloud-seed` copies its public lectures and the stage
   cache to the bucket. The cache is copied as it is: if the pipeline changed since the
   lectures were processed, process them again locally first (only the changed stages run),
   or the VM recomputes those stages, with Gemini and Modal calls, on its first boot.
6. On GitHub, add the repository variables `CLERK_PUBLISHABLE_KEY` and, from `make cloud-base`'s
   outputs, `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_DEPLOYER`.
   Push a tag (`git tag v0.6.0`, `git push origin v0.6.0`); once the release workflow has run,
   make the three `lecture-summariser-*` packages public in their GitHub settings, so the VM can
   pull them.
7. `make deploy tag=v0.6.0`, or the deploy workflow.

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
uv run lecture-eval --prepare --gate        # on a fresh stack: fetch the media, process Lecture 10, gate
uv run lecture-eval --suites notes --notes-file data/baselines/<video>/<run>/result.json   # score the Gemini baseline's notes
```

Each suite prints a summary, writes the details (every question, answer and verdict) to
`data/evals/<suite>/`, and records an `eval_runs` row: the suite, the dataset's hash, the git
commit, the models and settings, and the metrics. `evals/thresholds.json` holds the bounds the
gate checks, set a little below today's scores.

In CI, `.github/workflows/eval.yml` runs the same gate on GitHub's runner
([architecture](docs/architecture.md#eval-gate-in-ci-phase-4c)): the stack in Docker without a
GPU, Lecture 10 fetched and processed through the API (`--prepare`), then every suite, with
search scored without the reranker, which a CPU can't run at a usable speed. It runs on pushes
that touch the API, the pipeline, prompts, search or the evals, every Monday, and on demand,
and needs a `GEMINI_API_KEY` repository secret. The stage cache carries over between runs, so a
run only redoes what changed. The first run took 39 minutes, 24 of them processing the lecture
(20 transcribing it on the runner's four CPU cores); since then a run takes about 9 minutes,
with every stage of the lecture from the cache and 3 minutes for the suites, most of it the
answers. A run makes 72 LLM calls, the 36 answers and their grades: about $0.12 on Gemini's
paid tier. The free tier's 500 requests a day ran out during the first run, on a day that had
spent most of them labelling slides.

The captions and slide PDF aren't in git: they're the lecture's own material (CC BY-NC-SA).
Each dataset file names the file, its URL and its SHA-256; `--prepare` downloads what's missing
into `data/lectures/`. The answer judge uses
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

Mostly one lecture so far: MIT 6.0001 Lecture 10 (51 min), the one with eval datasets.
Lectures 11 and 12 are processed too, and the frame detector uses Lectures 1 to 12.

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
took 5 minutes on the CPU, so a fresh run took about 6 minutes, up from 2. Since Phase 5c the
embedding model runs on the GPU when there is one: Lecture 10 embeds in 1.4 s, and each search
that embeds the question is about 0.4 s faster than above ([speed](#speed)). More in
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
within what one lecture and one judge can separate. The vision LLM's missing titles came back in
the CI eval gate's first rehearsal (a batch of 8 of the routed slides), so a routed slide read
without a title now keeps OCR's.

### Frame detector

RF-DETR Nano ([ADR 0007](docs/adr/0007-rf-detr-for-the-frame-detector.md)), fine-tuned on
frames from ten MIT 6.0001 lectures and scored on two it never saw, one from each half of the
course: Lectures 6 and 12. No box was drawn by hand (`make detector-data`,
`make detector-train`): each slide frame is matched to its page of the lecture's slide PDF and
aligned to it by OCR'd text lines (median error under 2 pixels); the vision LLM boxes each
page's figures and annotations once (427 boxes on 396 pages), and those boxes carry onto every
frame that shows the page; a COCO-trained RF-DETR finds people. Frames the labeller can't be
sure of (a slide playing a video, a screen of live coding) are left out.

| | Train (Lectures 1-5, 7-11) | Valid (their last fifth) | Test (Lectures 6, 12) |
|---|---|---|---|
| Frames | 1,409 | 371 | 387 |
| Boxes: slide / person / figure / annotation | 639 / 762 / 199 / 314 | 201 / 176 / 66 / 56 | 188 / 204 / 56 / 33 |

On the test lectures, against their automatic labels (mAP50:95 0.71, mAP50 0.81):

| Class | AP50:95 | Precision | Recall |
|---|---|---|---|
| slide | 1.00 | 1.00 | 1.00 |
| person | 0.99 | 1.00 | 0.99 |
| annotation | 0.36 | 0.38 | 0.61 |
| figure | 0.34 | 0.67 | 0.36 |

Routing, deciding which slides go to the vision LLM, on the test lectures' 188 slide frames (65
with a figure or annotation by the labels):

| | Slides routed | Precision | Recall | F1 | On Lecture 12 alone |
|---|---|---|---|---|---|
| The 5a rule: ink outside text, angled lines, OCR confidence | 74 | 0.80 | 0.91 | **0.85** | 0.78 |
| The detector, confidence 0.5 | 54 | 0.87 | 0.72 | 0.79 | 0.68 |
| Either of them | 77 | 0.77 | 0.91 | 0.83 | |

Ten training lectures instead of two lifted the detector's routing F1 on Lecture 12 from 0.59
to 0.68, and figure AP from 0.15 on Lecture 12 to 0.34 on the two test lectures (annotation
AP, 0.50 on Lecture 12's 8 annotations then, is 0.36 on the 33 now). But the rule still routes
better: the detector misses more than a quarter of the slides with a figure or an annotation,
almost half of Lecture 12's, and routing when either says so doesn't beat the rule alone. So
the rule keeps routing slides, and the detector stays out of the pipeline. Finding slides and
people is solved at this scale, and the detector also recognises a slide playing a video, which
the brightness test takes for a camera shot. Every score here is against labels a model made,
not checked by hand, and the LLM's boxes aren't consistent (highlighted code sometimes counts
as a figure).

### Speed

`lecture-bench` (`make bench`) runs the three models the stack hosts itself every way they
could run, on the same laptop (RTX 3060 Laptop GPU with 6 GB, Ryzen 7 5800H), and scores every
variant, so one that gets faster by getting worse shows it. What the numbers decided is
[ADR 0008](docs/adr/0008-where-the-models-run.md); the raw results are saved in `data/bench/`.

**The embedding model** (Qwen3-Embedding-0.6B on Text Embeddings Inference): Lecture 10's 47
chunks (16,439 tokens), then each golden question on its own, searched by the dense vectors
alone.

| | Lecture 10 embedded | Tokens/s | A question, median | Recall@5 / MRR@10 / nDCG@10 | Cosine to the CPU's vectors | VRAM |
|---|---|---|---|---|---|---|
| CPU, fp32 (before) | 218 s | 75 | 437 ms | 0.97 / 0.87 / 0.85 | – | – |
| GPU, fp16 (now) | 1.4 s | 11,560 | 16 ms | 0.97 / 0.87 / 0.85 | 1.0000 | 1.5 GB |

The same vectors, 150 times faster, so Compose now runs the embedding model on the GPU when
there is one. It was the pipeline's slowest stage and the first step of every answer: through
the API, a reranked search now takes 0.43 s instead of 0.86, and a dense or hybrid one 30 to
40 ms instead of 0.43 s.

The benchmark also turned up a bug in TEI's CPU server: a text sent while other requests are in
flight sometimes comes back with the wrong vector. The benchmark's first run found the CPU's
chunk vectors at a mean cosine of 0.96 to the GPU's; rerun on an idle stack, 1.0000. Bursts of
15 overlapping requests (24 texts) went wrong in 3 of 12 on the CPU, with vectors at cosine
0.13 to 0.16 to the right ones, and in none of 60 on the GPU. Every vector in the index was
checked and is right, but on the CPU a question asked while a lecture is being embedded, or a
lecture embedded while questions are asked, can get wrong vectors.

**Speech recognition** (faster-whisper large-v3-turbo): Lecture 10 at each of CTranslate2's
compute types, scored against the human captions as the ASR eval scores it. The time is
recognition alone, after the model has loaded.

| Compute type | Load | Lecture 10 (51 min) | Real-time factor | VRAM added | WER | Technical terms |
|---|---|---|---|---|---|---|
| GPU, float16 | 14.3 s | 44 s | 0.014 | 3.3 GB | 2.8% | 99.6% |
| GPU, int8_float16 (the pipeline's) | 11.0 s | 40 s | 0.013 | 2.2 GB | 3.0% | 99.1% |
| GPU, int8 | 11.1 s | 42 s | 0.014 | 2.2 GB | 3.0% | 99.1% |
| CPU, int8 (4 threads) | 17.5 s | 938 s | 0.30 | – | 2.9% | 99.1% |

int8 weights take a third less VRAM than float16 and are no slower, for 0.2 points of WER. With
the reranker and the embedding model beside it the card peaks at 5.1 of its 6 GB, so float16,
1.1 GB more, wouldn't fit. Without a GPU a 51-minute lecture takes 16 minutes. In the pipeline
the stage takes 65 to 75 s: it also loads the model, and slide detection and OCR run beside it.

**The frame detector** (RF-DETR Nano at 384 px, [above](#frame-detector), as first trained on
Lectures 10 and 11): each of Lecture 12's 177 test frames through the network on its own, timed
without the preprocessing and decoding every variant shares, and scored against the frames'
automatic labels. Retraining on more lectures changes the weights, not the network, so the
timings stand.

| Runtime | Device | ms a frame, median | mAP50:95 | mAP50 | slide | person | figure | annotation |
|---|---|---|---|---|---|---|---|---|
| PyTorch, fp32 | CPU | 156.6 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| ONNX Runtime, fp32 | CPU | 122.4 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| ONNX Runtime, int8 dynamic | CPU | **68.9** | 0.645 | 0.720 | 0.99 | 0.99 | 0.14 | 0.46 |
| ONNX Runtime, int8 calibrated | CPU | 123.5 | 0.553 | 0.646 | 0.92 | 0.93 | 0.06 | 0.31 |
| PyTorch, fp32 | GPU | 17.6 | 0.649 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| PyTorch, fp16 | GPU | 22.3 | 0.650 | 0.719 | 1.00 | 0.99 | 0.14 | 0.47 |
| TensorRT, fp32 | GPU | 6.9 | 0.649 | 0.720 | 1.00 | 0.99 | 0.14 | 0.47 |
| TensorRT, fp16 | GPU | **3.8** | 0.648 | 0.720 | 1.00 | 0.99 | 0.14 | 0.46 |
| TensorRT, int8 calibrated | GPU | 7.5 | 0.569 | 0.644 | 0.94 | 0.95 | 0.11 | 0.28 |

At a frame a second, a 51-minute lecture is about 3,060 frames: 12 s of network time in
TensorRT fp16, 54 s in PyTorch on the GPU, 3.5 minutes on the CPU in int8 and 6 in fp32. So
when the detector joins the pipeline it runs as a TensorRT fp16 engine, 4.6 times faster than
PyTorch at the same accuracy.

- **fp16 doesn't help PyTorch.** One frame at a time, the Nano model is too small to keep the
  GPU busy: PyTorch launches its operations one by one, and the launching takes longer than
  the arithmetic. TensorRT compiles the network into fewer, fused kernels.
- **int8 pays off only on the CPU, and only dynamic.** Dynamic int8 (weights stored in int8,
  each activation quantized as it arrives) is 1.8 times faster than fp32 for 0.004 of mAP.
  Calibrated int8 fixes each activation's scale in advance from training frames, which TensorRT
  requires; it loses 0.08 to 0.10 of mAP in both runtimes, probably because the vision
  transformer's activations have outliers one fixed scale per tensor can't cover, and in
  TensorRT it's slower than fp32: only the convolutions and matrix multiplies are int8, and
  converting in and out of them costs more than it saves. Making the rest of it fp16 as well,
  tried separately, broke its accuracy (mAP 0.10).
- The mAP here comes from the benchmark's own decoding, the same for every variant: 0.649 for
  the model trained on two lectures, against 0.66 from RF-DETR's own test pass on it.

## Commands

| Command | What it does |
|---|---|
| `make up` / `make down` | Start or stop Postgres, SeaweedFS, Temporal, Qdrant and the embedding server, on the GPU if `nvidia-smi` finds one (`make reset` also deletes their data) |
| `make app` | Build and run the API, the web app and the CPU worker in Docker |
| `make gpu-worker` | Build and run the GPU services in Docker: speech recognition and the reranker |
| `make observability` | Start Grafana with traces, metrics and logs on http://localhost:3001 (set `OTEL_ENDPOINT` to send to it) |
| `make worker` | Run a CPU worker on the host instead |
| `make api` | Run the API on the host with auto-reload |
| `make web` | Run the web app on the host with hot reload (Node 24) |
| `make openapi` | Regenerate the web app's typed API client after an API change |
| `make eval` | Run every eval suite against the running stack, record it, and check the thresholds |
| `make eval-retrieval` | Score search only |
| `make detector-data` | Label frames for the frame detector from the lectures' videos and slide PDFs (installs PyTorch, 2 GB, the first time) |
| `make detector-train` | Fine-tune the frame detector on the GPU and score the held-out lecture |
| `make bench` | Benchmark speech recognition, the embedding model and the frame detector, before and after (needs the stack with the GPU services, Lecture 10 processed and the detector trained; installs TensorRT, 2.3 GB, the first time) |
| `make modal-model` / `make modal` | Once, the speech model into Modal / deploy speech recognition and the embedding model to GPUs in Modal |
| `make cloud-base` / `make cloud-secrets` / `make cloud-seed` | Set up the demo in Google Cloud: its bucket, network and deploy access / its secrets / its lectures and the stage cache ([above](#deploying-the-demo)) |
| `make deploy tag=v0.6.0` / `make destroy` | Make the demo's VM for a release, or delete it |
| `make migrate` | Apply migrations |
| `make revision m="add chapters"` | Generate a migration after changing `packages/core/src/lecture_core/models.py` |
| `make test` / `make test-unit` | All tests / unit tests only |
| `make lint` / `make fmt` / `make typecheck` | Ruff check / Ruff fix and format / mypy (strict) |
| `make audit` | pip-audit on the locked dependencies |
| `make check` | Lint, type-check and test, like CI |

## Layout

```
apps/api/                  FastAPI service (routes, schemas, dependencies)
apps/web/                  Next.js web app (library, lecture and course pages; shadcn/ui); typed client from openapi.json
packages/core/             settings, SQLAlchemy models, Alembic migrations, S3 client
packages/perception/       PyAV media reading, slide detection, speech recognition
packages/llm/              Pydantic AI agents: read slides, chapters, notes
packages/pipeline/         stages, stage cache, timeline, local runner, Temporal workflow and workers
packages/rag/              chunks, encoders (TEI, BM25), Qdrant index, hybrid search
evals/                     eval suites (lecture-eval), datasets, thresholds, Gemini baseline
ml/detector/               frame detector: labels from slide PDFs, dataset, RF-DETR training
ml/bench/                  speed benchmarks (lecture-bench): speech recognition, embeddings, the detector
prompts/                   versioned prompts (pipeline, Q&A and baseline)
data/                      lecture videos and run outputs (not in git)
tests/unit/                fast tests, no Docker
tests/integration/         real Postgres, SeaweedFS, Temporal and Qdrant via testcontainers
infra/compose.yaml         local stack (`app` profile adds the API, web app and CPU worker)
infra/compose.gpu.yaml     the embedding server on the GPU, added by the Makefile when there is one
infra/grafana/             Grafana datasource and dashboard (JSON), loaded by `make observability`
infra/docker/              Dockerfiles: API, worker (CPU and GPU variants)
infra/compose.cloud.yaml   the demo's stack on one VM, behind Caddy (infra/caddy/)
infra/modal/               speech recognition and the embedding model on GPUs in Modal
infra/terraform/           the demo in Google Cloud: base (once) and demo (the VM, per session)
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
- **The reranker and the embedding model run on the GPU locally**, not the CPU: on a laptop
  CPU the reranker took 77 seconds to rerank one query's passages, embedding a lecture took
  minutes (seconds on the GPU), and TEI's CPU server can return a wrong vector when requests
  overlap. Together they need 2.8 GB of VRAM beside speech recognition. Without a GPU the
  embedding model stays on the CPU; set `SEARCH_MODE=hybrid`
  ([ADR 0005](docs/adr/0005-qdrant-for-hybrid-search.md),
  [ADR 0008](docs/adr/0008-where-the-models-run.md)).
- **Uploads are a single presigned PUT** (up to 5 GiB), not resumable multipart through Uppy.
  Worth adding when uploads get large or flaky.
- **The player streams the uploaded MP4 directly** (a presigned URL with range requests) rather
  than HLS renditions. Transcoding to HLS comes back if other formats or adaptive bitrate are
  needed.
- **CI builds the images (API, CPU worker, web) but doesn't scan or push them.** That comes with
  deployment in Phase 6.
- **Slides are routed to the vision LLM by simple image measures until the detector exists.**
  The blueprint routes by the detector's figure, table and equation boxes; until Phase 5b, ink
  outside the OCR'd lines, lines at an angle and OCR confidence stand in
  ([architecture](docs/architecture.md#ocr-and-routing-phase-5a)). OCR uses the PP-OCRv6 models
  that come with RapidOCR rather than the PP-OCRv5 the blueprint names: newer, and nothing more
  to download.
- **The eval gate runs every suite in CI, on the CPU.** The blueprint runs a small subset on
  pull requests and the full suite nightly; here the stage cache makes the full suite cheap
  enough for every push that could change a score, plus a weekly run. Without a GPU, speech
  recognition runs on the CPU and the reranker's bounds are left to `make eval` on a machine
  with one ([architecture](docs/architecture.md#eval-gate-in-ci-phase-4c)).
- **No Vercel, and no permanent demo.** The web app runs on the demo's VM, next to the API at
  one HTTPS address, and the VM exists only while it's needed: Terraform makes it for a
  session and deletes it after, so a session costs cents
  ([ADR 0010](docs/adr/0010-on-demand-demo-on-google-cloud-and-modal.md)).
- **The demo's Q&A needs an account.** The blueprint's public demo has live Q&A for
  anyone; here visitors browse and search freely and sign in to ask, so every LLM call
  belongs to a user with a quota ([ADR 0009](docs/adr/0009-clerk-sign-in-and-quotas.md)).
- **No MLflow.** Eval runs are recorded in Postgres (`eval_runs`) and charted in Grafana;
  detector training runs and benchmark results are files under `data/` (RF-DETR's logs,
  `data/bench/*.json`), and the numbers that matter are in this README. MLflow would come in
  with more training runs than a handful, or a registry for the detector once it's in the
  pipeline.
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
- [x] **Phase 4, evals and observability**
  - [x] 4a: eval suites (search, answers with an LLM judge, speech recognition, notes),
        `eval_runs`, thresholds and a local gate (`make eval`)
  - [x] 4b: OpenTelemetry traces, metrics and logs, a Grafana dashboard, LLM tracing (Langfuse
        optional), cost per answer and per run
  - [x] 4c: the eval gate in CI: every suite on GitHub's runner (CPU, no reranker), Lecture 10
        fetched and processed through the API, the stage cache carried between runs
- [ ] **Phase 5, CV and optimisation**
  - [x] 5a: OCR on every slide (RapidOCR), the vision LLM only for slides with figures,
        annotations or doubtful OCR, and a slides eval against the slide PDF
  - [ ] 5b: a frame detector (RF-DETR, [ADR 0007](docs/adr/0007-rf-detr-for-the-frame-detector.md))
        for slides, people, figures and annotations, on frames labelled automatically from the
        slide PDFs: trained on 10 lectures and scored on 2. It finds slides and people, but
        routes slides worse than the 5a rule (F1 0.79 against 0.85), so it isn't in the
        pipeline
  - [x] 5c: speed benchmarks before and after (`make bench`): the embedding model moved to the
        GPU (150 times faster), speech recognition stays int8, and the detector would run as a
        TensorRT fp16 engine ([ADR 0008](docs/adr/0008-where-the-models-run.md))
- [ ] **Phase 6, ship**
  - [x] 6a: sign-in in the API (Clerk tokens), public demo lectures and private uploads,
        per-user quotas and a daily LLM budget
  - [x] 6b: sign-in in the web app (Clerk's sign-in windows, the token on every call, quotas
        shown)
  - [ ] 6c: the demo on an on-demand Google Cloud VM (Terraform), uploads transcribed on GPUs
        in Modal, release images scanned and pushed on a tag, deploys from Actions
        ([ADR 0010](docs/adr/0010-on-demand-demo-on-google-cloud-and-modal.md)): built and
        rehearsed locally; the first cloud deploy is next
  - [ ] 6d: results write-up, diagram and screenshots

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
