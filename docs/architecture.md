# Architecture

The target design is in [blueprint.md](blueprint.md). This page describes what exists now and
changes as each phase lands.

## Current state: Phase 1 (foundation)

```
 client ──(1) POST /v1/lectures ────────► FastAPI (apps/api) ──► Postgres (lectures table)
   │         (3) POST .../complete-upload        │
   │                                             │ (3) HEAD the object, check its size
   │                                             ▼
   └──(2) PUT file to presigned URL ─────► SeaweedFS (S3 API, bucket "lectures")
```

- **apps/api**: a stateless FastAPI service. It creates lecture records, hands out presigned upload
  URLs, and confirms uploads by checking the stored object. It never handles video bytes.
- **packages/core**: settings, the SQLAlchemy models and Alembic migrations, and the object storage
  client. Shared by the API now and by the workers later.
- **packages/pipeline**: the content-addressed stage cache ([ADR 0001](adr/0001-stage-cache.md)).
  Stage functions and, in Phase 2, Temporal workflows go here.

### Upload flow

1. `POST /v1/lectures` stores a lecture with status `awaiting_upload` and returns a presigned PUT
   URL for `raw/{lecture_id}/source.{ext}`.
2. The client uploads the file straight to storage.
3. `POST /v1/lectures/{id}/complete-upload` checks the object exists and is within the size limit,
   then sets the status to `uploaded`. Calling it again returns the same result.

### Lecture status

`awaiting_upload → uploaded → processing → ready`, and `failed` from any processing step. Phase 1
uses the first two.

## Next: Phase 2 (vertical slice)

- `workers/cpu` and `workers/gpu`: Temporal workers whose activities call stage functions in
  `packages/pipeline` through the stage cache.
- `POST /v1/lectures/{id}/process` starts the `ProcessLecture` workflow. Progress streams over SSE.
- `apps/web`: a Next.js lecture page with player, chapters and synced transcript. Uploads move to
  multipart through Uppy.
- New tables: `pipeline_runs`, `stage_artifacts`, `transcript_segments`, `slides`,
  `timeline_segments`, `chapters`, `summaries`.
