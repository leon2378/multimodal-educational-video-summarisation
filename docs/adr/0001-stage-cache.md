# 0001: Content-addressed stage cache in object storage

- Status: Accepted
- Date: 2026-09-27
- Code: `packages/pipeline/src/lecture_pipeline/cache.py`

## Context

Processing a lecture takes seven stages (blueprint section 4), and some are expensive: ASR costs GPU
minutes, the vision LLM and summaries cost tokens. Most of the project's work is changing one stage
(a prompt, a model, a threshold) and measuring the effect. Doing that cheaply means re-running only
the stages whose output can change. Retries after a crash should also skip finished work.

## Decision

Every stage output is stored under a key that hashes everything that determines it:

```
key = sha256(canonical_json({stage, version, model, params, inputs}))
```

- `inputs` maps each input's role to a content hash (for the uploaded file) or to the upstream
  stage's cache key. Keys therefore chain: change the summary prompt and the summary key changes,
  and so does the key of every stage downstream of it. ASR's key doesn't change.
- Canonical JSON (sorted keys, no whitespace, no NaN) makes the key independent of dict order.
- Outputs are Pydantic models. They're stored at `artifacts/{stage}/{key}.json` inside an envelope
  that records the stage, version, model, params, inputs and creation time.
- Object storage is the source of truth. A `stage_artifacts` table in Postgres will index it once the
  workflow needs to query artifacts (Phase 2); it can always be rebuilt from storage.
- Stages that produce large binaries (HLS renditions, frames, slide images) write them under
  `media/`, `frames/` or `slides/`. Their JSON artifact lists those keys and hashes.

## Alternatives considered

- **Temporal's event history.** It replays completed activities within one workflow run, but not
  across runs, so it doesn't help when you re-run a lecture with a new prompt.
- **DVC pipelines.** Good for offline experiments on files; they don't fit a service that processes
  uploads on request. DVC is still the plan for datasets.
- **JSON in Postgres rows.** Simple, but bloats the database with large transcripts and timelines.

## Consequences

- Re-running with a changed prompt or model only recomputes affected stages, and retries are free.
- Eval runs can point at exact artifacts, which makes results reproducible.
- `version` has to be bumped whenever a code change can change a stage's output. If you forget, you
  get stale results. A changed output schema at least fails loudly, because cached entries no longer
  validate. A later improvement is hashing the stage's source code into the key.
- Two workers racing on one key both compute, and the last write wins. For deterministic stages the
  results are identical. For LLM stages, either output is a valid result for that key.
- The cache only grows. Garbage collection (delete keys no current lecture run references) can come
  once storage cost matters.
