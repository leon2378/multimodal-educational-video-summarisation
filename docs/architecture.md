# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 6 under way (the on-demand cloud demo built, its first run next)

A lecture goes from upload in the browser to study notes: the web app uploads straight to
storage and asks the API to process; the API starts a Temporal workflow; workers run the
pipeline stages; the results land in Postgres and the search index; the web app shows them in
step with the video, searches them and answers questions about them, for one lecture or across
a course. The same stages also run on a local file without any of that (`lecture-process`,
which stops before search). Eval suites score each part through the API, and the API and
workers report traces, metrics and logs over OpenTelemetry. With sign-in on, the API checks
Clerk's session tokens: visitors read and search the public lectures, and signed-in users ask
and upload within quotas. A demo of it all runs on a Google Cloud VM made for a session and
deleted after, with uploads transcribed on GPUs in Modal.

```
 client ── upload (presigned PUT) ──────────────────────────────► SeaweedFS
   │                                                                 ▲  ▲
   ├── POST /v1/lectures/{id}/process ─► FastAPI ─► Temporal         │  │ stage cache,
   ├── GET  .../events (SSE progress)       │         │ ProcessLecture  │  │ artifacts
   └── GET  .../notes|transcript|slides     │         ├─ cpu queue ─► CPU worker ─┤ (media, slides,
                                            ▼         │                │           timeline, save)
                                         Postgres ◄───┼────────────────┤
                                                      │                └─► TEI (embeddings, GPU) ─► Qdrant
                                                      ├─ llm queue ─► CPU worker (Gemini calls)
                                                      └─ gpu queue ─► GPU worker (speech, 1 at a time)

 client ── GET /v1/search ─► FastAPI ─► TEI (query embedding) + BM25 ─► Qdrant ─► TEI reranker (GPU)
 client ── POST .../ask ───► FastAPI ─► rewrite (LLM) ─► search ─► Postgres (sentences, slides)
                                 ◄── SSE ── answer (LLM, streamed) ─► citations checked ─► Postgres
          (/v1/lectures/{id}/... or /v1/courses/{id}/..., which search the course's lectures)
```

### Upload path (Phase 1)

```
 client ──(1) POST /v1/lectures ────────► FastAPI (apps/api) ──► Postgres (lectures table)
   │         (3) POST .../complete-upload        │
   │                                             │ (3) HEAD the object, check its size
   │                                             ▼
   └──(2) PUT file to presigned URL ─────► SeaweedFS (S3 API, bucket "lectures")
```

1. `POST /v1/lectures` stores a lecture with status `awaiting_upload` and returns a presigned PUT
   URL for `raw/{lecture_id}/source.{ext}`. Given the file's size, it refuses a file over the
   limit before it's sent.
2. The client uploads the file straight to storage. The API never handles video bytes.
3. `POST /v1/lectures/{id}/complete-upload` checks the object exists and is within the size limit,
   then sets the status to `uploaded`. Calling it again returns the same result.

**In parts, to resume** ([ADR 0012](adr/0012-resumable-uploads-in-parts.md)), for any client
that knows the file's size:

- **Step 2 becomes `POST /v1/lectures/{id}/upload-parts`**, with the file's size. It starts a
  multipart upload and returns a presigned URL for each 16 MiB part. The client PUTs the parts
  straight to storage, several at a time.
- **Resuming:** asked again, after an interruption or once the URLs expire, upload-parts says
  which parts storage has and gives URLs for the rest. Storage's own list is what counts, so the
  browser keeps nothing but the file.
- **Each part's URL is signed for its length**, so an upload can't grow past the size it started
  with.
- **`complete-upload` joins the parts** once storage has every one, and until then answers 409
  saying how many are missing.
- **Unfinished uploads:** deleting the lecture drops its parts. On the demo's bucket, a
  lifecycle rule clears any upload left unfinished for a week, and resuming it then starts over.

Lecture status: `awaiting_upload → uploaded → processing → ready`, or `failed` when a run
fails. A ready or failed lecture can be processed again.

Or `POST /v1/lectures/from-url` with a link ([ADR 0011](adr/0011-lectures-from-any-link.md)): the
lecture starts out processing, and the workflow's first stage, `fetch`, downloads the video to
where an upload would be. It's skipped when the video is there already, so processing again
doesn't download again. yt-dlp does the downloading, which covers direct links and over a
thousand sites, YouTube included (its JavaScript challenges need Deno, which runs without
network access).

- **The link is checked twice**, in the API and in the worker (`lecture_core.links`): http or
  https, no credentials, no IP address off the public internet, no private-network name.
- **The downloader runs in its own process**, with none of the worker's secrets in its
  environment. Each connection it opens is checked on the address it reaches, so redirects and
  DNS answers pointing inside are refused too. That keeps the metadata server, Postgres, Qdrant
  and the rest of the stack out of reach.
- **Limits**:
  - the uploader's byte limit, enforced by yt-dlp, by `RLIMIT_FSIZE` and by a final size check;
  - videos of up to 3 hours, and an hour for the download;
  - a playlist's first video only, and no live streams.
- **The file is probed like an upload** before it's stored, so a link to anything but a lecture
  video never ends up where its owner could download it back.
- **The lecture is filled in from the site**: its title (unless one was given), its licence
  (YouTube reports one) and its attribution.
- **Formats:** one file with picture and sound when the site has one, up to 720p where there's a
  choice. YouTube serves the two apart: the downloader then fetches the picture (H.264 at up to
  720p) and the sound in the original language one after the other, and `media.join` copies
  both into one MP4 with PyAV. The images have no FFmpeg command line for yt-dlp to merge them
  with.
- **YouTube** refuses many cloud servers. The lecture then fails, saying to upload the file
  instead, unless `YOUTUBE_COOKIES_B64` (a signed-in account's cookies) or `YOUTUBE_PROXY` gets it
  through.

`DELETE /v1/lectures/{id}` (its owner or an admin, and not while it's processing) removes the
lecture's points from the search index, its video from storage, then its row, which takes its
results, processing history and conversations with it (the foreign keys cascade). The external
steps come first and are safe to repeat, so after a failure the lecture is still there to
delete again. The stage cache stays: it's keyed by content, so another upload of the same video
shares it, and it's the only copy of what processing paid for.

### Web app (Phase 2c)

`apps/web`, Next.js 16 with TanStack Query, Tailwind and shadcn/ui (Radix primitives, copied
into `src/components/ui`). The browser calls the API directly (CORS allows the web origin)
through a client generated from the API's OpenAPI schema, and loads the video and slide images
straight from storage through presigned URLs, cached for half an hour so images don't reload on
every refetch. Colours are CSS variables in `globals.css`, one set per theme; a script in
`<head>` sets the theme before the first paint.

- **Library** (`/`): upload in parts straight to storage (`lib/upload.ts`): four parts at a
  time, each tried again after a pause, and fresh URLs from `upload-parts` for any that expired
  or that joining finds missing; progress counts the parts storage has and those on their way.
  Then it starts processing and opens the lecture. An upload that stops is continued from the
  lecture's page by choosing the same file again (its size must match), so it needs nothing kept
  in the browser; cancelling deletes the lecture, and leaving the page mid-upload asks first. The
  upload dialog is app-wide, so a video dropped on any page opens it, and so does Add lecture on
  a course page (with that course chosen). It also adds a lecture from a link
  (`POST /v1/lectures/from-url`).
- **Lecture** (`/lectures/{id}`): a stream on `/events`, read with `fetch` so it can carry
  the session token (`EventSource` can't send headers) and reconnecting with backoff, shows each
  stage while it processes and refreshes the page's data when the run ends. The video's
  `timeupdate` drives everything else: the transcript line (a binary search over sentence start
  times), the current slide (the span containing the time, so camera shots keep the last slide)
  and the current chapter. Every timestamp seeks the video. The transcript's search box searches
  the lecture and plays each hit from where it starts. The study tabs stay mounted while hidden,
  so an answer keeps streaming and a search stays put on another tab; the lists re-render only
  when the line, slide or chapter playing changes, not on every `timeupdate`.
- Transcript lines are sentences: runs split batched ASR segments at sentence ends using word
  timings when saving results, without touching the cached ASR output.
- **Ctrl+K**: a command palette that matches lecture and course titles as you type and, after a
  pause, runs `/v1/search` across every lecture.
- **Sign-in** (Phase 6): with a Clerk publishable key built in, `@clerk/nextjs` provides the
  sign-in and sign-up windows and the account button, and every API call carries the session
  token as a bearer token: the generated client's middleware, the Q&A and progress streams, and
  the upload's API calls (not its parts, which go to storage). Calls wait up to 8 s for Clerk to
  load, so a visit's first requests don't go out signed out. `GET /v1/me` drives what's shown:
  nothing about sign-in when the API has it off, sign-in prompts for anonymous visitors, and the
  quotas left. Without the key, the app runs as before.

### Processing (Phase 2b)

1. `POST /v1/lectures/{id}/process` records a `pipeline_runs` row and starts the
   `ProcessLecture` workflow, with workflow id `process-{lecture_id}`. While a run is in progress,
   asking again returns that run; a partial unique index keeps it to one running run per lecture.
2. The workflow runs each stage as an activity on its queue: `cpu` (probe, audio, slides, OCR,
   timeline, notes assembly, embedding and indexing for search, saving), `gpu` (speech
   recognition, one activity at a time for a 6 GB card, with heartbeats) and `llm` (slide
   reading, chapters, notes drafts). Speech recognition runs in parallel with the slides
   (detection, OCR, then reading), and embedding in parallel with the notes.
3. Activities hand each other stage-cache refs, never payloads: a 51-minute transcript with word
   timings can pass Temporal's 2 MB limit. Results and files live in the stage cache in object
   storage, so a retried activity, or a re-run with one prompt changed, reuses everything else.
4. The lecture is indexed for search, then the last activity replaces its `transcript_segments`,
   `slides`, `timeline_segments` and `summaries` rows in one transaction and marks it ready. A
   failure marks the run and lecture failed. Bad input (not a video) isn't retried.
5. `GET /v1/lectures/{id}/events` streams progress as server-sent events: the workflow's
   `progress` query while it runs, then the stored run.

Results: `GET /v1/lectures/{id}/transcript`, `/slides` (with presigned image URLs), `/timeline`
and `/notes`, plus `/runs` for each run's stage timings and LLM usage.

### Search (Phase 3a)

Code in `packages/rag`; the choice of Qdrant is [ADR 0005](adr/0005-qdrant-for-hybrid-search.md).

1. **Chunks**: one per timeline segment (30 to 90 s of speech on one slide), the slide's title and
   text followed by the transcript. Lecture 10 gives 47.
2. **Embed** (a cached stage on the cpu queue, in parallel with the notes): a dense vector from
   Qwen3-Embedding-0.6B served by Text Embeddings Inference (TEI), on the GPU when there is one
   (Phase 5c), and BM25 term weights from FastEmbed. Queries get an instruction prefix; passages
   don't.
3. **Index** (cpu queue, before the lecture is marked ready): one Qdrant point per chunk, with the
   lecture id, times, slide, chapter and text as payload. Point ids come from the lecture and
   segment ids; new points are written before stale ones are deleted.
4. **Search** (`GET /v1/search?q=...&lecture_id=...`): `dense`, `bm25`, `hybrid` (top 30 from
   each, fused by reciprocal rank with k = 60), or `rerank` (hybrid, then bge-reranker-v2-m3 on
   TEI reorders the 30). Ties are ordered by position in the lecture, so results repeat.
   `SEARCH_MODE` sets the default: `rerank` in Compose, where the reranker runs on the GPU
   (a search takes about 0.4 s with the embedding model there too, 0.8 s with it on the CPU),
   and `hybrid` without a GPU, where reranking takes over a minute.

`retrieval-eval` (`make eval-retrieval`) asks the golden questions in
`evals/datasets/golden-qa/` through the API and scores each mode: Recall@5, MRR@10 and nDCG@10.
A hit counts when its segment overlaps an answer span by at least 3 seconds.

### Q&A (Phase 3b)

Code in `apps/api/src/lecture_api/routes/qa.py`, `packages/llm` (`qa`) and `packages/core`
(`qa`); prompts in `prompts/qa/`.

1. `POST /v1/lectures/{id}/ask` saves the question, in a new thread or the one named by
   `thread_id`, and returns a stream of server-sent events.
2. A follow-up is rewritten to stand on its own, from the thread's last 3 answered exchanges
   ("why does that help?" becomes "why does memoisation help?"), because search sees only the
   question. A first question skips this call.
3. Search (`SEARCH_MODE`) gives the top 6 segments. Their sentences come from the lecture's
   transcript rows and their slides from its slide readings, so the model sees each sentence with
   its `[mm:ss]`.
4. The answer model gets the passages in delimited, HTML-escaped blocks, with the conversation
   for context, and the lecture's outline: its chapters with their start times, from the study
   notes. Search finds passages by meaning, so it can't find "the last topic": for that question
   it returned the introduction, which previews the whole lecture, and the answer cited its
   first seconds. The model is told to use only the passages and outline, to cite by copying a
   sentence's time (or, for a question about the lecture's order, the chapter's start), and to
   say when the lecture doesn't cover the question (`prompts/qa/answer.v2.md`). The answer
   streams out as it's written.
5. Every `[mm:ss]` in the answer is checked: valid if it falls inside a retrieved segment or on a
   chapter's start. The web app makes valid ones play the video and strikes the others through.
6. The answer is saved with its sources, citations, model, tokens, time to first token and total
   time. If search or the model fails, the answer is saved with the reason, and the stream ends
   with an error event instead of done.

Readers rate answers with thumbs up or down and an optional reason (`POST /v1/feedback`, one
rating per answer), which Phase 4 turns into eval cases. Answers render through a small Markdown
parser in the web app that produces text nodes only, and KaTeX runs with `trust` off, so an
answer can't inject HTML.

On Lecture 10, with `gemini-3.5-flash-lite` on the free tier and the reranker on the GPU, the
first words arrived 2.4 to 4.7 seconds after asking in three tries (search, a rewrite for the
follow-up, then the model's first chunk), and the rest within half a second: the model sends
short answers in a few large chunks.

### Courses (Phase 3)

A course groups lectures (`courses`, and `lectures.course_id`): a lecture joins one when it's
uploaded or later from its page (`PATCH /v1/lectures/{id}`), and deleting a course keeps its
lectures.

- `GET /v1/search?course_id=...` searches the course's lectures, and `POST /v1/courses/{id}/ask`
  answers from its processed ones, in threads of their own (a database check keeps each thread
  to one lecture or one course).
- The index isn't told about courses: a course search filters by the ids of the lectures in the
  course when it runs. So moving a lecture between courses needs no re-indexing. The blueprint
  put `course_id` in each point's payload instead, which would have to be rewritten on every
  move.
- A time alone is ambiguous across lectures, so a course answer labels each lecture L1, L2, ...
  in the order its passages first come up, shows each sentence as `[L2 12:34]`, and cites the
  same way (`prompts/qa/course-answer.v1.md`). The check maps the label back to the lecture's
  passages, and each source carries its lecture's label and title, so the web app can open the
  right lecture at the cited moment (`/lectures/{id}?t=seconds`).

On a course holding Lecture 10 and a short Lecture 1 exercise, "why was nothing shown on the
console in the print exercise?" was answered from the exercise with a valid `[L1 01:04]`, which
opens that lecture at 1:04. A big-O question was answered from Lecture 10 with citations like
`[L1 29:30]`: there Lecture 10 was L1, because its passages came up first.

### Evals (Phase 4a)

`lecture-eval` (package `evals`) runs suites against the API, so it scores what users get:

| Suite | Measures | Ground truth |
|---|---|---|
| retrieval | Recall@5, MRR@10, nDCG@10 per search mode | golden questions with answer spans |
| answers | correctness and faithfulness (LLM judge, `prompts/evals/judge-answer.v2.md`, given what the answer model saw: the passages with their slides, and the outline), citations inside the passages and near the answer span, declining uncovered questions, latency, tokens | the same golden set, with two questions about the lecture's order that the retrieval suite leaves out |
| asr | WER against the captions (`jiwer`), technical-term recall, real-time factor | the lecture's captions |
| notes | concept citations against where the captions say the term; structural checks | the lecture's captions |

- Both sides of a comparison with captions are normalised the same way
  (`lecture_evals.captions`): lower case, speaker labels and bracketed notes removed, hyphens
  split. Each caption word gets a time spread evenly over its cue.
- The answer suite asks each question in a new thread and deletes it afterwards
  (`DELETE /v1/threads/{id}`). The judge sees the question, the reference answer, the passages
  the answer was given (rebuilt from its sources and the transcript) and the answer, and
  returns whether it declined, a verdict, whether every claim is supported, and why.
- Every run is an `eval_runs` row: suite, dataset path and SHA-256, git commit (`-dirty` with
  local changes), config (models, prompt fingerprints, modes) and flat metrics.
  `evals/thresholds.json` bounds the metrics that matter; `--gate` exits 1 past them.

### Eval gate in CI (Phase 4c)

`.github/workflows/eval.yml` runs the suites on GitHub's runner and fails the build past
`evals/thresholds.json`: on pushes to main (and pull requests) that touch the API, the
pipeline, prompts, search or the evals, every Monday (the hosted LLM can change under us), and
on demand.

- **The stack** is the Compose file with `infra/compose.eval.yaml` on top. The runner has no
  GPU, so one worker serves every queue with speech recognition on the CPU (the worker image's
  `asr` extra: faster-whisper without CUDA), and search runs hybrid: reranking takes over a
  minute a query on a CPU, so the reranker isn't started and `--modes dense,bm25,hybrid` leaves
  its thresholds out of the gate.
- **The lecture**: `lecture-eval --prepare` downloads what the datasets name and CI doesn't have
  (the video and captions from the Internet Archive's mirror of the course, the slide PDF from
  OCW; each dataset gives the URL and SHA-256), then uploads and processes Lecture 10 through
  the API as a user would, and the suites run on the result.
- **Caches**: the speech model, the embedding model and the lecture's media are cached by
  version. The stage cache (`artifacts/` in object storage) is synced out after each run and
  back in before the next, so only stages whose inputs changed run again: a change to a prompt
  reruns the stages that use it, and nothing else. The first run took 39 minutes, 24 of them
  processing the lecture (speech recognition 20 minutes on the runner's four CPU cores,
  embedding 3); with the cache a run takes about 9 minutes, 15 s of it processing and 3 minutes
  the suites, mostly the answers suite's LLM calls. The rest is preparing the runner and
  starting the stack.
- **The LLM** is Gemini on the paid tier, through the `GEMINI_API_KEY` repository secret: a run
  makes 72 calls (36 answers and their grades), about $0.12. The free tier's 500 requests a day
  are shared with development, and ran out during the first run, after a day of labelling
  slides. Pull requests from forks don't get the secret, so the job skips them.
- **Results** go to the job summary (each suite's tables and whether it's within its bounds) and
  the reports to a workflow artifact. The `eval_runs` rows stay in the runner's database.

### Observability (Phase 4b)

```
 API ──────────┐  OTLP/HTTP (OTEL_ENDPOINT)   ┌─ Tempo (traces) ─────┐
 CPU worker ───┼────────────────────────────► │  Prometheus (metrics) ├─► Grafana :3001
 GPU worker ───┘   traces, metrics, logs      │  Loki (logs)          │   (+ Postgres: eval_runs,
       │                                      └── grafana/otel-lgtm ──┘    pipeline_runs, feedback)
       └── pydantic-ai spans only ─► Langfuse (when its keys are set)
```

- `lecture_core.telemetry.setup` installs the tracer, meter and logger providers when
  `OTEL_ENDPOINT` or the Langfuse keys are set; otherwise nothing is recorded. Instrumented:
  - FastAPI (not health checks, and not each chunk of a stream), httpx, SQLAlchemy and logging;
  - Temporal, through its `TracingInterceptor` on the API's client and the workers, so a
    process request, its workflow and the activities on both workers are one trace;
  - Temporal's worker metrics (queue waits, slots), tagged `worker`;
  - Pydantic AI's GenAI spans, with prompts, replies and tokens but not images.
- The app's metrics (`lecture_core.metrics`) and where they're recorded:

  | Metric (Prometheus name) | Labels | Recorded in |
  |---|---|---|
  | `lecture_stage_duration_seconds`, `lecture_stage_runs_total` | stage, cached | each activity (`_measured`) |
  | `lecture_llm_tokens_total`, `lecture_llm_cost_usd_total` | model, purpose (stage or `qa`), direction | `lecture_llm.telemetry.record_usage` after each LLM stage and answer |
  | `lecture_qa_first_token_milliseconds`, `lecture_qa_duration_milliseconds`, `lecture_qa_answers_total` | scope (lecture or course), outcome | the ask route |
  | `lecture_feedback_total` | rating | the feedback route |
  | `lecture_gpu_memory_used_bytes` | | the GPU worker, through NVML |

- Costs come from `lecture_llm.pricing` (paid-tier prices, dated). Answers carry `cost_usd`;
  a pipeline run's `llm_usage` holds the cost of the LLM calls behind its notes, cached ones
  included, which is what "cost per lecture-hour" divides.
- The dashboard is `infra/grafana/dashboards/lecture-summariser.json`. Counts use `anchored`
  ranges and Prometheus writes a zero at each series' start, so sparse traffic gives exact
  numbers ([ADR 0006](adr/0006-opentelemetry-to-grafana-and-langfuse.md)).
- Measured on the stack: a question's trace has the query embedding (about 0.8 s on the
  CPU), Qdrant, reranking (0.4 s on the GPU) and the Gemini call. A fully cached run of the
  exercise clip takes 2.4 s end to end. Lecture 10's notes cost $0.041 in LLM calls at
  paid-tier prices, $0.047 per lecture-hour; an answer costs about $0.0015.

### Pipeline stages (Phase 2a)

The stages the workers run. `lecture-process <video>` also runs them in order on a local file
(`make process` runs it in the GPU worker image). Each stage goes through the stage cache ([ADR 0001](adr/0001-stage-cache.md)),
so a second run only redoes stages whose inputs, version, model, params or prompt changed.

```
 video ─┬─► probe ──────────────────────────────┐
        ├─► audio (16 kHz FLAC) ─► asr ─────────┤ transcript with word timestamps
        └─► slides (frames at 1 fps) ─► ocr ─► read_slides (OCR, or the vision LLM when routed) ─┐
                                                 ▼                                               ▼
                                             timeline ◄─────────────────────────────────────────┘
                                                 ▼
                                   chapters (LLM) ─► notes (LLM: map per chapter, then reduce)
                                                 ▼
                                    StudyNotes: TL;DR, chapters, concepts, formulas, quiz
```

| Stage | Package | What it does |
|---|---|---|
| probe, audio | perception (`media`) | PyAV, which bundles FFmpeg: metadata, 16 kHz mono FLAC |
| asr | perception (`asr`) | faster-whisper large-v3-turbo, int8, VAD and word timestamps. On the GPU in Docker |
| slides | perception (`slides`) | Frames at 1 fps, split into slide vs camera by brightness, a 256-bit difference hash to find changes, keeps the most complete frame of each slide, recognises revisits |
| ocr | perception (`ocr`) | RapidOCR (PP-OCRv6 small, ONNX Runtime) on every slide: text lines with boxes and confidence, and how much ink isn't text |
| read_slides | perception (`ocr`) + llm | Title and text from OCR; slides with figures, annotations or doubtful OCR go to the vision LLM, 8 per request, for title, text, figure, LaTeX, code (`SLIDE_READER`, [below](#ocr-and-routing-phase-5a)) |
| timeline | pipeline (`fuse`) | One segment per slide span, split at 90 s. Speech before the first slide gets no slide |
| chapters, notes | llm + pipeline (`assemble`) | Chapter plan, notes per chapter, then TL;DR and quiz. Output cites segment ids, converted to times. Concepts get the time the term is first said, from word timestamps |

- **Interfaces**: speech models sit behind `Transcriber`, and LLM calls go through Pydantic AI, so
  the model is a setting (`LLM_MODEL`, `WHISPER_*`).
- **Untrusted content**: transcripts and slide text go into HTML-escaped, delimited blocks, and every
  prompt says they are content, not instructions.
- **Untrusted files**: FFmpeg reads hundreds of formats, and its vulnerabilities tend to be in the
  ones nobody needs. `media` has it open only MP4/MOV, Matroska and WebM files
  (`format_whitelist`) and start only the decoders lectures use while it probes a file
  (`codec_whitelist`: H.264, HEVC, VP8/9, AV1, MPEG-4; AAC, MP3, Opus, Vorbis, FLAC, ALAC, AC-3,
  PCM), and decodes a stream only if its decoder is one of those. Anything else fails the probe
  stage with a reason.
- **Output**: `data/pipeline-runs/<video>/<time>/` holds `notes.md` and `result.json` (stage timings,
  cache hits, LLM usage, notes). The notes use the same `StudyNotes` format as the Gemini baseline,
  so the two can be compared directly.

### OCR and routing (Phase 5a)

Every slide is OCR'd, and only the slides OCR can't handle go to the vision LLM
(`lecture_perception.ocr`, `stages.ocr_slides` and `stages.read_slides`).

- **OCR**: RapidOCR with the PP-OCRv6 small models it ships with, on ONNX Runtime, 4 threads on
  the CPU worker: 12 s for Lecture 10's 24 slides on an idle worker (32 s in the first run, on
  a machine busy after a rebuild). Per slide it keeps each text line with its corners and
  confidence, where the slide sits in the frame, and how much of the slide is ink outside the
  text lines.
- **Routing**: a slide goes to the vision LLM when it has
  - ink outside text beyond the deck's usual (its 25th percentile, which absorbs the template's
    bands and rules) by more than 0.3% of the slide: a plot, diagram, table or marks;
  - 2 or more lines at over 8°: annotations written across the slide;
  - a mean OCR confidence under 0.93, or fewer than 5 words.

  On Lecture 10 that's 11 of 24 slides; the reasons are kept with the readings. Every slide
  the vision LLM described a figure on is among them, except one with only faint arrows.
- **OCR readings**: the title is the tall text starting in the top 30% of the slide, over as
  many lines as it runs; the rest is text, bullets as "- ". Lines in the bottom 8% (the
  footer) are dropped. A reading records who made it (`slides.reader`: `ocr` or `vlm`). A
  slide the vision LLM returns without a title (Gemini sometimes drops a whole batch's) keeps
  OCR's: a run of the eval gate found 8 of 24 slides untitled that way.
- **Modes**: `SLIDE_READER` is `routed` (the default), `vlm` (every slide, as before 5a) or
  `ocr`. `vlm` keeps the cache key slide reading always had, so lectures read before 5a aren't
  read again.
- **Evaluated** by the `slides` suite against the lecture's slide PDF, and by the other suites
  on the notes and answers made from each reading (the comparison is in the README's
  results). Routed reading matched or beat the vision LLM on slide text, search and answers,
  at 55% of its cost; the notes, written again from the new reading, cited 9 of 11 checkable
  concepts near where they're said, against 9 of 10.
- **The slides no longer wait for speech recognition**: reading needs only the slides. In a
  fresh run of Lecture 10 speech recognition took 75 s and slide detection 42 s, and reading
  (16 s) started after both. Now detection, OCR (12 s idle) and routed reading (11 s) run
  beside speech recognition, about 65 s against its 75.

### Frame detector (Phase 5b)

RF-DETR Nano ([ADR 0007](adr/0007-rf-detr-for-the-frame-detector.md)) finding four things in a
video frame: the slide, people, figures and annotations. It lives in `ml/detector`
(`lecture-detector`), outside the pipeline; results are in the README.

```
 slide PDF ─► pages rendered ─► vision LLM boxes figures, annotations (once per page, saved)
     │                                              │
     └─ text lines ─┐                               ▼
 video ─► slides + OCR ─► align: page ↔ frame (affine, RANSAC) ─► every page drawn into
          (the pipeline's code)                                   frame geometry
 video ─► frames every 2 s ─► match each to a page ─► slide frame: slide box + the page's
                                                      boxes it shows; camera frame; or left out
                                     COCO RF-DETR ─► person boxes ─► COCO dataset, split by lecture
```

- **Alignment**: each slide found in the video is matched to the PDF page it shares most words
  with; OCR lines that read like the page's lines give point pairs, and one affine transform per
  lecture fits them (median error 1.1 to 1.9 pixels across the twelve lectures). It squeezes
  the page horizontally (the video's pixels are 4:3) and crops its margins. Screens of live
  coding also match a page by their words, but don't fit the transform, so where slides sit in
  the frame (the area frames are compared over) comes from the slides that do: in Lectures 2, 3
  and 8, screens of code outnumber the slides.
- **Which frames**: a frame is a slide when it's bright and correlates with a page (blurred,
  over the slide area) at 0.6 or more, or at 0.35 or more and the page is the one OCR found for
  that stretch of video (a slide mid-build). A dark frame with no slide title band is a camera
  shot. The rest is left out rather than guessed: a slide playing a video, a code demo. Each
  page gives at most 6 frames, 10 s apart; camera shots one every 20 s.
- **Labels**: the slide box is the slide area; a page's region goes onto a frame only where the
  frame shows at least half its ink (slides build up); tables count as figures (10 in 396
  pages). People come from RF-DETR's COCO weights, on every frame kept.
- **Splits**: Lectures 1 to 5 and 7 to 11 train, the last fifth of each validates, and
  Lectures 6 and 12 test, one from each half of the course.
- **Scoring**: RF-DETR's test pass gives AP per class; `lecture-detector evaluate` asks the
  routing question (figure or annotation, or not) of the detector and of the 5a rule, each test
  lecture being the rule's deck.

### Speed (Phase 5c)

`ml/bench` (`lecture-bench`, `make bench`) measures the three models the stack runs itself,
every way they could run, on one laptop, and scores each variant with the eval suites' own
measures. The tables are in the README; what they decided is
[ADR 0008](adr/0008-where-the-models-run.md).

- **Embeddings** (`lecture-bench embeddings`): Lecture 10's chunks as the index holds them,
  embedded by two TEI servers the command starts and stops, one on the CPU and one on the GPU,
  each as Compose runs it (the same model and revision; the GPU one in the reranker's image),
  whichever the stack itself is running. Tokens are counted by TEI's tokenizer, questions are
  embedded one at a time, search is scored as the retrieval eval scores it (dense only), and
  the two sets of vectors are compared. VRAM is sampled through NVML every 50 ms.
- **Speech recognition** (`lecture-bench asr`): Lecture 10's audio, extracted as the pipeline
  extracts it, transcribed inside the GPU worker's image (which has CUDA and faster-whisper;
  the script is mounted into a `docker compose run`) by the pipeline's own transcriber, once
  per compute type. A second of silence loads the model first, so the time is recognition
  alone; the card's memory is sampled while it runs, and the transcript is scored like the ASR
  eval.
- **Frame detector** (`lecture-bench detector`): RF-DETR exports the trained model to ONNX;
  ONNX Runtime's tools make an fp16 version and two int8 ones: dynamic (weights stored in
  int8, activations quantized as they arrive) and calibrated (fixed scales from 65 training
  frames, written into the model as QuantizeLinear/DequantizeLinear pairs around the
  convolutions and matrix multiplies). TensorRT 11 builds engines from the fp32, fp16 and
  calibrated files, and they're kept beside them: it has no fp16 or int8 switches, and takes
  each layer's precision from the model. Every variant shares RF-DETR's preprocessing and
  decoding, so only the network differs; it's timed alone, a frame at a time, over the test
  lecture's 177 frames after 10 to warm up, and scored by COCO mAP against the automatic labels.
- **What changed**: the embedding model runs on the GPU when there is one:
  `infra/compose.gpu.yaml` swaps its server for TEI's GPU image, and the Makefile adds the file
  when `nvidia-smi` finds a GPU. Its vectors are the CPU's (mean cosine 1.0000), so the embed
  stage's cache key stays as it was and moving between the two re-embeds nothing. With speech
  recognition running beside the reranker and the embedding model, the card peaks at 5.1 of its
  6 GB. Speech recognition stays at int8_float16, and the detector, once in the pipeline, would
  run as a TensorRT fp16 engine.

### Sign-in and quotas (Phase 6)

`lecture_api.auth`, `access` and `quotas`; the choices are
[ADR 0009](adr/0009-clerk-sign-in-and-quotas.md).

- **Tokens**: with `AUTH_ISSUER` set, a request may carry the issuer's session token as a
  bearer token. The API checks its RS256 signature against the issuer's published keys
  (`{issuer}/.well-known/jwks.json`, fetched once and cached), its expiry, its issuer, the
  origin Clerk issued it for (`azp`, against `AUTH_AUTHORIZED_PARTIES`), and an audience for
  issuers that set one. Its `sub` names the user, who gets a `users` row on their first request
  (with email and name if the token carries them). No token: anonymous. A bad one: 401, so a
  lapsed session is told to sign in again rather than quietly shown less.
- **Who sees what**: lectures and courses have an owner and a visibility. Anonymous visitors read
  and search the public ones; a signed-in user also their own; admins (`ADMIN_USERS`, by `sub`)
  everything. Asking, uploading, processing and making courses need sign-in; changing a lecture
  or course needs its owner or an admin, and only admins make things public. Search filters by
  the lectures the caller may read, and so does a course's lecture list. Threads and ratings
  are their user's. Unreadable things answer 404; readable but not yours, 403.
- **Quotas**: counted per UTC day from a usage ledger (`usage_events`), written as things
  happen: a user's questions (30 a day, 5 a minute), their lectures (3 a day, each up to 1 GB,
  checked when the upload is confirmed), and everyone's LLM spend: each answer's tokens at
  paid-tier prices, plus what each processing run spent on the LLM stages it didn't take from
  the cache (recorded by the worker). Deleting a lecture or a conversation leaves the ledger
  alone, so it gives nothing back; counting the rows themselves, as at first, let a user upload,
  delete and upload again. Past $2 a day, questions and processing wait for the next day.
  Limits answer 429 with `Retry-After`; `GET /v1/me` reports them.
- **Off by default**: without an issuer, every request is one local user with no limits, and
  what it makes is public: development, the tests and the eval gate run as before. The browser
  sends the token with `fetch` (CORS allows `Authorization` and exposes `Retry-After`),
  including on the progress stream (see the web app, above). Sign-in is checked before the
  other dependencies, so an anonymous caller hears 401 rather than, say, 503 for a missing
  language model.

### The demo in the cloud (Phase 6c)

`infra/terraform/`, `infra/compose.cloud.yaml`, `infra/modal/` and `lecture_api.demo`; the
choices are [ADR 0010](adr/0010-on-demand-demo-on-google-cloud-and-modal.md).

```
 browser ── https://<ip>.sslip.io ──► Caddy ─┬─ /v1, /healthz, /readyz ──► API ──► Postgres, Qdrant,
                                             │                                    CPU embeddings (questions)
                                             └─ everything else ─────────► web app
 browser ── presigned PUT / GET ──────────────────────────────────────────► Cloud Storage
 worker (cpu, llm, gpu queues) ──► Gemini · Modal: speech recognition, embeddings (indexing)
```

- **One VM per session**: Terraform's `demo` part is a single e2-standard-2 VM; `base`, applied
  once, has what outlasts it: the bucket, the VM's service account and its two secrets, a
  network open on 80 and 443 (and to SSH only through Google's IAP proxy), and the Workload
  Identity Federation that lets the deploy workflow in. At boot the startup script installs
  Docker, writes the Compose file and Caddyfile from the instance's metadata, builds the
  stack's `.env` from the VM's IP (`SITE_ADDRESS`), the release tag and Secret Manager, and
  starts it.
- **One origin**: Caddy gets a Let's Encrypt certificate for `<ip with dashes>.sslip.io` and
  sends the API's paths to the API and the rest to the web app, which is built with an empty
  API URL so it calls its own origin; server-sent events go through unbuffered. Media skip it:
  presigned URLs go straight to the bucket, which allows any origin to use them. The bucket
  clears uploads in parts left unfinished for 7 days, whose parts would otherwise be billed
  unseen.
- **The demo lectures**: `lecture-demo export` (on a dev machine, against the local stack)
  writes the public, processed lectures and their course to `demo/` (videos and a manifest) and
  the stage cache to `artifacts/`, as laid out in the bucket. On the VM's first boot, with
  sign-in off, `lecture-demo load` makes each course and lecture through the API, has the
  bucket copy the video to where the API expects the upload, confirms it and processes it:
  every stage is a cache hit, so the four lectures take about 20 seconds. Then the API
  restarts with sign-in on, and only then does Caddy start, so nothing outside reaches the API
  while sign-in is off. Rehearsed on a dev machine (the same images and Compose file, local
  storage for the bucket, the GPU embedding server for Modal's), the stack answered 140 s after
  starting, most of it the CPU embedding server warming up on two cores.
- **Modal**: the worker runs every queue. Its gpu-queue transcriber is a `ModalTranscriber`,
  which streams the audio to a Modal function on an L4 running the GPU worker's code and model,
  and relays the progress it sends back as heartbeats. Indexing embeds through
  `EMBEDDINGS_URL`, the embedding server's GPU image as a Modal web endpoint behind proxy auth
  (`EMBEDDINGS_HEADERS`); the API embeds questions on the VM's CPU server, one at a time, with
  small batches so its warm-up fits next to the stack.
- **Releases and deploys**: `release.yml` builds the three images on a version tag, scans them
  with Grype (exceptions, each with its reason, in `.grype.yaml`), and pushes them to GHCR; run
  by hand, it only builds and scans. Each image's final stage runs `apt-get upgrade` and is never
  taken from the build cache, so a release has Debian's latest security fixes even before its
  base image is rebuilt. `deploy.yml` (run by hand) applies or destroys `demo`
  and, on deploy, waits until `/v1/me` reports sign-in on. Deploying another release replaces
  the VM (`replace_triggered_by`), since its startup script reads the release only at boot; a
  metadata change alone would leave the old images running. Destroying also deletes the
  session's uploads (`raw/`) from the bucket.

### Known limitations

- Slide detection assumes light slides on a dark hall, as in MIT OCW recordings. Two slides with the
  same template and layout can merge: in 6.0001 Lecture 10, "Law of Addition" and "Law of
  Multiplication" become one. Phase 5's detector (crop the slide, mask the presenter) is meant to
  fix both. OCR's slide area and ink measure make the same light-slide assumption.
- OCR reads lines top to bottom, so text written across a slide interleaves with the slide's
  own lines. Routing sends such slides to the vision LLM; with `SLIDE_READER=ocr` one answer
  in Lecture 10's eval missed a bullet that way. Routing thresholds were set on one lecture.
- No verification pass yet (flagging claims the cited segments don't support). It comes with the
  eval suites in Phase 4.
- Chunks follow slides, and lecturers often start the next topic before changing slide. In
  Lecture 10 the explanation of primitive operations (12:46 to 13:15) is spoken under the
  previous slide, so its chunk leads with the wrong slide text, and search ranks the next
  segment (which shows the right slide) first. Chunking by sentences with some overlap, or
  indexing slides on their own, would help.
- With sign-in off (the local default), anyone who can reach the API can ask questions and
  spend the LLM budget, so Compose binds the API to 127.0.0.1. With it on, the quotas are soft:
  two requests at the same moment can both pass. An answer the client disconnects from isn't
  saved.
- A question is searched once, and the answer uses the top 6 segments. A two-part question
  across a course can get all six from one lecture: asked both how to check what code prints
  (the Lecture 1 exercise) and how to compare algorithms (Lecture 10), the answer covered the
  second and said the course doesn't seem to cover the first. Splitting such questions, or
  keeping a segment from each lecture that matches well, would help.
- Lectures processed before search existed (Phase 3a) aren't indexed. Processing one again
  indexes it, and only the embedding and indexing steps run: the rest is cached.
- Without a GPU, embedding runs on the CPU, at about 75 tokens a second on a Ryzen 7 5800H:
  3.5 minutes for a 51-minute lecture on an idle machine, the slowest stage, and a lecture is
  ready only once it's indexed. The GPU takes 1.4 s.
- TEI's CPU server (1.9.4) sometimes returns the wrong vector for a text sent while other
  requests are in flight: in 3 of 12 bursts of 15 overlapping requests, texts came back with
  vectors at cosine 0.13 to 0.16 to their own (short questions, where it was recorded), and one
  input per forward pass (`--max-batch-requests 1`) didn't stop it. On the CPU, then, a question
  asked while a lecture is being embedded, or a lecture embedded while questions are asked, can
  get wrong vectors. The GPU server got 60 such bursts right, and every vector in the index
  was checked. Worth reporting to TEI.

## Next: shipping (Phase 6), and a detector that routes as well as the rule

Phase 6c's code is built and rehearsed locally; the first deploy to Google Cloud and Modal
comes next, which also settles whether Clerk's development instance accepts an sslip.io origin.
Then 6d: the results write-up, a diagram and screenshots.

The detector finds slides (AP 1.00) and people (0.99) on the held-out lectures, and a slide
playing a video, which the brightness test misses; but trained on ten lectures it still routes
slides worse than the 5a rule (F1 0.79 against 0.85, up from 0.59 on two): it misses figures and
annotations, and the LLM's page boxes it learns from aren't consistent. What could close the
gap: hand-checked labels for the test lectures (so the scores measure the detector, not the
labeller), RF-DETR Small (ADR 0007's next step), and lectures from other courses. Once it
routes as well as the rule, the pipeline can run it instead of both the brightness test and
the ink measure, as a TensorRT fp16 engine on the GPU worker: about 12 s for a 51-minute
lecture's frames. Lectures filmed with a projector in the room would need a transform per
frame (a homography), and chalkboard lectures a board class.
