# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 2b (orchestration and storage)

A lecture goes from upload to study notes through the API: the API starts a Temporal workflow,
workers run the pipeline stages, and the results land in Postgres. The same stages also run
on a local file without any of that (`lecture-process`).

```
 client ── upload (presigned PUT) ──────────────────────────────► SeaweedFS
   │                                                                 ▲  ▲
   ├── POST /v1/lectures/{id}/process ─► FastAPI ─► Temporal         │  │ stage cache,
   ├── GET  .../events (SSE progress)       │         │ ProcessLecture  │  │ artifacts
   └── GET  .../notes|transcript|slides     │         ├─ cpu queue ─► CPU worker ─┤ (media, slides,
                                            ▼         │                │           timeline, save)
                                         Postgres ◄───┼────────────────┘
                                                      ├─ llm queue ─► CPU worker (Gemini calls)
                                                      └─ gpu queue ─► GPU worker (speech, 1 at a time)
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

### Processing (Phase 2b)

1. `POST /v1/lectures/{id}/process` records a `pipeline_runs` row and starts the
   `ProcessLecture` workflow, with workflow id `process-{lecture_id}`. While a run is in progress,
   asking again returns that run; a partial unique index keeps it to one running run per lecture.
2. The workflow runs each stage as an activity on its queue: `cpu` (probe, audio, slides,
   timeline, notes assembly, saving), `gpu` (speech recognition, one activity at a time for a
   6 GB card, with heartbeats) and `llm` (slide reading, chapters, notes drafts). Speech
   recognition and slide detection run in parallel.
3. Activities hand each other stage-cache refs, never payloads: a 51-minute transcript with word
   timings can pass Temporal's 2 MB limit. Results and files live in the stage cache in object
   storage, so a retried activity, or a re-run with one prompt changed, reuses everything else.
4. The last activity replaces the lecture's `transcript_segments`, `slides`,
   `timeline_segments` and `summaries` rows in one transaction and marks the lecture ready. A
   failure marks the run and lecture failed. Bad input (not a video) isn't retried.
5. `GET /v1/lectures/{id}/events` streams progress as server-sent events: the workflow's
   `progress` query while it runs, then the stored run.

Results: `GET /v1/lectures/{id}/transcript`, `/slides` (with presigned image URLs), `/timeline`
and `/notes`, plus `/runs` for each run's stage timings and LLM usage.

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

## Next: Phase 2c

`apps/web`: a Next.js lecture page with the player, chapters, a transcript that follows
playback, and the slides, reading the API above. Uploads move to multipart through Uppy.
