# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 2a (pipeline stages)

Two separate paths exist so far: the API handles uploads, and the pipeline runs on a local file.
Phase 2b joins them with Temporal workers.

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

Lecture status: `awaiting_upload → uploaded → processing → ready`, and `failed` from any
processing step. Only the first two are used so far.

### Pipeline (Phase 2a)

`lecture-process <video>` runs every stage in order on a local file (`make process` runs it in the
GPU worker image). Each stage goes through the stage cache ([ADR 0001](adr/0001-stage-cache.md)),
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

## Next: Phase 2b and 2c

- **2b**: Temporal workers (`workers/cpu`, `workers/gpu`) whose activities call these same stage
  functions with object storage in place of the local directory. `POST /v1/lectures/{id}/process`
  starts the `ProcessLecture` workflow, with progress over SSE. New tables for runs, timeline,
  chapters and notes.
- **2c**: `apps/web`, a Next.js lecture page with the player, chapters, a transcript that follows
  playback, and the slides. Uploads move to multipart through Uppy.
