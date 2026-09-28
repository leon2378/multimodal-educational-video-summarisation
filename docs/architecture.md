# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 3 under way (search done, cited answers next)

A lecture goes from upload in the browser to study notes: the web app uploads straight to
storage and asks the API to process; the API starts a Temporal workflow; workers run the
pipeline stages; the results land in Postgres and the search index; the web app shows them in
step with the video and searches them. The same stages also run on a local file without any of
that (`lecture-process`, which stops before search).

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
2. The workflow runs each stage as an activity on its queue: `cpu` (probe, audio, slides,
   timeline, notes assembly, embedding and indexing for search, saving), `gpu` (speech
   recognition, one activity at a time for a 6 GB card, with heartbeats) and `llm` (slide
   reading, chapters, notes drafts). Speech recognition and slide detection run in parallel, and
   so do embedding and the notes.
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

### Pipeline stages (Phase 2a)

The stages the workers run. `lecture-process <video>` also runs them in order on a local file
(`make process` runs it in the GPU worker image). Each stage goes through the stage cache ([ADR 0001](adr/0001-stage-cache.md)),
so a second run only redoes stages whose inputs, version, model, params or prompt changed.

```
 video ─┬─► probe ──────────────────────────────┐
        ├─► audio (16 kHz FLAC) ─► asr ─────────┤ transcript with word timestamps
        └─► slides (frames at 1 fps) ─► read_slides (vision LLM) ─┐
                                                 ▼                 ▼
                                             timeline ◄───────────┘
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
| read_slides | llm | Vision LLM, 8 slides per request: title, text, figure, LaTeX, code |
| timeline | pipeline (`fuse`) | One segment per slide span, split at 90 s. Speech before the first slide gets no slide |
| chapters, notes | llm + pipeline (`assemble`) | Chapter plan, notes per chapter, then TL;DR and quiz. Output cites segment ids, converted to times. Concepts get the time the term is first said, from word timestamps |

- **Interfaces**: speech models sit behind `Transcriber`, and LLM calls go through Pydantic AI, so
  the model is a setting (`LLM_MODEL`, `WHISPER_*`).
- **Untrusted content**: transcripts and slide text go into HTML-escaped, delimited blocks, and every
  prompt says they are content, not instructions.
- **Output**: `data/pipeline-runs/<video>/<time>/` holds `notes.md` and `result.json` (stage timings,
  cache hits, LLM usage, notes). The notes use the same `StudyNotes` format as the Gemini baseline,
  so the two can be compared directly.

### Known limitations

- Slide detection assumes light slides on a dark hall, as in MIT OCW recordings. Two slides with the
  same template and layout can merge: in 6.0001 Lecture 10, "Law of Addition" and "Law of
  Multiplication" become one. Phase 5's detector (crop the slide, mask the presenter) is meant to
  fix both.
- No verification pass yet (flagging claims the cited segments don't support). It comes with the
  eval suites in Phase 4.
- Chunks follow slides, and lecturers often start the next topic before changing slide. In
  Lecture 10 the explanation of primitive operations (12:46 to 13:15) is spoken under the
  previous slide, so its chunk leads with the wrong slide text, and search ranks the next
  segment (which shows the right slide) first. Chunking by sentences with some overlap, or
  indexing slides on their own, would help.
- Embedding runs on the CPU, at about 95 tokens a second on a Ryzen 7 5800H: 5 minutes for a
  51-minute lecture, the slowest stage. A lecture is ready only once it's indexed, so a fresh
  run takes about 6 minutes rather than 2. The GPU could do it in seconds, next to speech
  recognition if VRAM allows.

## Next: the rest of Phase 3

Answers: `POST /v1/lectures/{id}/ask` retrieves the top segments, streams an LLM answer over SSE
with `[mm:ss]` citations checked against the retrieved segments, and stores threads, messages and
feedback. Then a chat panel on the lecture page, and courses, so search and answers can span
several lectures.
