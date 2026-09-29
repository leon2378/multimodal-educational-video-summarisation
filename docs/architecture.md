# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 5 under way (OCR and routing done, the detector next)

A lecture goes from upload in the browser to study notes: the web app uploads straight to
storage and asks the API to process; the API starts a Temporal workflow; workers run the
pipeline stages; the results land in Postgres and the search index; the web app shows them in
step with the video, searches them and answers questions about them, for one lecture or across
a course. The same stages also run on a local file without any of that (`lecture-process`,
which stops before search). Eval suites score each part through the API, and the API and
workers report traces, metrics and logs over OpenTelemetry.

```
 client ── upload (presigned PUT) ──────────────────────────────► SeaweedFS
   │                                                                 ▲  ▲
   ├── POST /v1/lectures/{id}/process ─► FastAPI ─► Temporal         │  │ stage cache,
   ├── GET  .../events (SSE progress)       │         │ ProcessLecture  │  │ artifacts
   └── GET  .../notes|transcript|slides     │         ├─ cpu queue ─► CPU worker ─┤ (media, slides,
                                            ▼         │                │           timeline, save)
                                         Postgres ◄───┼────────────────┤
                                                      │                └─► TEI (embeddings) ─► Qdrant
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
   URL for `raw/{lecture_id}/source.{ext}`.
2. The client uploads the file straight to storage. The API never handles video bytes.
3. `POST /v1/lectures/{id}/complete-upload` checks the object exists and is within the size limit,
   then sets the status to `uploaded`. Calling it again returns the same result.

Lecture status: `awaiting_upload → uploaded → processing → ready`, or `failed` when a run
fails. A ready or failed lecture can be processed again.

### Web app (Phase 2c)

`apps/web`, Next.js 16 with TanStack Query and Tailwind. The browser calls the API directly
(CORS allows the web origin) through a client generated from the API's OpenAPI schema, and loads
the video and slide images straight from storage through presigned URLs.

- **Library** (`/`): upload with progress (a presigned PUT from the browser), then it starts
  processing and opens the lecture.
- **Lecture** (`/lectures/{id}`): an `EventSource` on `/events` shows each stage while it
  processes and refreshes the page's data when the run ends. The video's `timeupdate` drives
  everything else: the transcript line (a binary search over sentence start times), the current
  slide (the span containing the time, so camera shots keep the last slide) and the current
  chapter. Every timestamp seeks the video. A search tab searches the lecture and plays each hit
  from where it starts.
- Transcript lines are sentences: runs split batched ASR segments at sentence ends using word
  timings when saving results, without touching the cached ASR output.

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
   Qwen3-Embedding-0.6B served by Text Embeddings Inference (TEI), and BM25 term weights from
   FastEmbed. Queries get an instruction prefix; passages don't.
3. **Index** (cpu queue, before the lecture is marked ready): one Qdrant point per chunk, with the
   lecture id, times, slide, chapter and text as payload. Point ids come from the lecture and
   segment ids; new points are written before stale ones are deleted.
4. **Search** (`GET /v1/search?q=...&lecture_id=...`): `dense`, `bm25`, `hybrid` (top 30 from
   each, fused by reciprocal rank with k = 60), or `rerank` (hybrid, then bge-reranker-v2-m3 on
   TEI reorders the 30). Ties are ordered by position in the lecture, so results repeat.
   `SEARCH_MODE` sets the default: `rerank` in Compose, where the reranker runs on the GPU
   (a search takes about 0.8 s), and `hybrid` without a GPU, where reranking takes over a minute.

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
   for context. It is told to use only the passages, to cite by copying a sentence's time, and to
   say when the lecture doesn't cover the question. The answer streams out as it's written.
5. Every `[mm:ss]` in the answer is checked: valid if it falls inside a retrieved segment. The
   web app makes valid ones play the video and strikes the others through.
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
| answers | correctness and faithfulness (LLM judge, `prompts/evals/judge-answer.v1.md`), citations inside the passages and near the answer span, declining uncovered questions, latency, tokens | the same golden set |
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
- A real CI gate needs the processed lecture and a Gemini key in CI; that's Phase 4c.

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
  footer) are dropped. A reading records who made it (`slides.reader`: `ocr` or `vlm`).
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
- Until auth arrives in Phase 6, anyone who can reach the API can ask questions and spend the
  LLM quota; Compose binds the API to 127.0.0.1. An answer the client disconnects from isn't
  saved.
- A question is searched once, and the answer uses the top 6 segments. A two-part question
  across a course can get all six from one lecture: asked both how to check what code prints
  (the Lecture 1 exercise) and how to compare algorithms (Lecture 10), the answer covered the
  second and said the course doesn't seem to cover the first. Splitting such questions, or
  keeping a segment from each lecture that matches well, would help.
- Lectures processed before search existed (Phase 3a) aren't indexed. Processing one again
  indexes it, and only the embedding and indexing steps run: the rest is cached.
- Embedding runs on the CPU, at about 95 tokens a second on a Ryzen 7 5800H: 5 minutes for a
  51-minute lecture, the slowest stage. A lecture is ready only once it's indexed, so a fresh
  run takes about 6 minutes rather than 2. The GPU could do it in seconds, next to speech
  recognition if VRAM allows.

## Next: the detector (Phase 5b)

A detector for the slide region, the presenter, and figures, tables and equations: it crops the
slide before change detection (fixing the merged slides), masks the presenter, and replaces the
ink measure in routing. It needs frames from more lectures, labelled by hand (500 to 1,000,
split by lecture), and a choice between YOLO26 (AGPL-3.0) and RF-DETR (Apache-2.0), which gets
its ADR. Slide-boundary precision and recall need hand-labelled slide changes too. Then the
speed benchmarks (5c). The eval gate in CI (4c) waits for more lectures and somewhere to keep
them.
