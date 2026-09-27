# 0002: Temporal for pipeline orchestration, from Phase 2

- Status: Accepted
- Date: 2026-09-27

## Context

Each lecture runs a multi-stage workflow lasting minutes to an hour. It needs:

- per-stage timeouts, retries with backoff, and heartbeats during long GPU work
- separate queues for CPU, GPU and LLM work, with the GPU queue limited to one task at a time on a
  6 GB card
- to survive worker crashes (a GPU out-of-memory error, a WSL restart) without starting over
- progress reporting to the UI, cancellation, and re-running a single stage

## Options

| Option | For | Against |
|---|---|---|
| **Temporal** | Durable workflow state, per-activity retry and timeout policies, task queues, signals and queries, a UI for inspecting runs | Another service to run; workflow code must be deterministic; learning curve |
| Celery + Redis | Familiar, light | Task-level, not workflow-level: multi-step chains with partial re-runs get fragile, and state after a crash lives only in your own bookkeeping |
| Dagster / Prefect | Strong for scheduled data pipelines | Asset- and schedule-oriented; heavier fit for per-request workflows started by an API call |
| asyncio + a status column | No new infrastructure | You end up rebuilding retries, timeouts, heartbeats and recovery by hand |

## Decision

Use Temporal, with one `ProcessLecture` workflow per lecture and task queues `cpu`, `gpu` (with
`max_concurrent_activities=1`) and `llm`. Run `temporal server start-dev` locally.

Temporal is added in Phase 2, not Phase 1. Stages are written first as plain functions that go
through the stage cache (ADR 0001). Activities then become thin wrappers around them, so the stage
logic stays unit-testable without a Temporal server.

## Consequences

- A crashed worker resumes from the last completed activity. Combined with the stage cache, a
  re-run also skips work finished in earlier runs.
- Workflow code can't do I/O or read the clock directly. All of that goes in activities.
- Deployment needs Temporal too: self-hosted on the demo VM, or Temporal Cloud if scaling out.
