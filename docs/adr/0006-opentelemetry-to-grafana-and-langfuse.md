# 0006: OpenTelemetry into a local Grafana stack, LLM calls to Langfuse

- Status: Accepted
- Date: 2026-09-29
- Code: `packages/core/src/lecture_core/telemetry.py`, `lecture_core/metrics.py`,
  `packages/llm/src/lecture_llm/telemetry.py`, `infra/grafana/`

## Context

Phase 4 needs to show where time and money go (blueprint sections 6 and 14): stage durations,
cache hits, queue waits, GPU memory, LLM tokens and cost, Q&A time to first token, feedback. A
slow answer or a failed run should be traceable from the HTTP request through the Temporal
workflow and its activities on two workers to the LLM call, with the prompt and reply. It all
has to run on a laptop for free, beside a stack that already uses most of a 6 GB GPU.

## Options

- **A vendor SDK** (Logfire, Datadog, the Langfuse SDK on its own). Least setup, and Logfire
  understands Pydantic AI natively. But the code would be written against one backend, and the
  hosted ones need an account and a network connection to see anything locally.
- **OpenTelemetry SDK, sending OTLP to Grafana's `otel-lgtm` image**: an OpenTelemetry
  collector, Tempo (traces), Prometheus (metrics), Loki (logs), Pyroscope and Grafana in one
  container. Built for development, not production, and the image is 0.9 GB. Because the app
  only speaks OTLP, the backend can change later (Grafana Cloud, a real collector) without code
  changes.
- **The same, as separate containers** (collector, Tempo, Prometheus, Loki, Grafana): five
  services to configure and keep in step, for the same result on one machine.
- **Jaeger plus Prometheus**: traces and metrics, but no logs, and two UIs.
- **For LLM calls, self-hosted Langfuse**: version 3 needs Postgres, ClickHouse, Redis and S3,
  heavier than the rest of this stack. **Langfuse Cloud** takes OTLP directly, and its free plan
  covers a solo project.

## Decision

- The API and workers use the OpenTelemetry SDK and send OTLP over HTTP to `OTEL_ENDPOINT`. With
  it unset (the default), telemetry is off and tests don't touch it.
- Instrumentation:
  - FastAPI, httpx (TEI, Qdrant, Gemini), SQLAlchemy and Python logging.
  - Temporal's `TracingInterceptor`, so a process request, its workflow and its activities on
    both workers form one trace.
  - Temporal's own worker metrics (queue waits, slots in use), tagged with the worker.
  - Pydantic AI's instrumentation: spans and metrics that follow the OpenTelemetry GenAI
    conventions, with prompts, replies and token counts. Images are left out, so slide
    frames don't fill the spans.
- The app's own metrics live in `lecture_core.metrics`:
  - stage durations and runs, by whether the cache answered;
  - LLM tokens and cost, by model and purpose;
  - Q&A time to first token and answer time;
  - answers and ratings;
  - GPU memory in use.
- Cost uses paid-tier prices (`lecture_llm.pricing`, dated) even on the free tier, so the
  numbers say what the pipeline would cost. Each answer and each pipeline run stores its cost.
- Locally, `grafana/otel-lgtm` 0.33.1 runs under the `observability` Compose profile. The
  dashboard is JSON in `infra/grafana/dashboards/`. A Postgres datasource charts `eval_runs`
  and `pipeline_runs` next to the live metrics.
- Prometheus runs with two feature flags. Traffic here is sparse: a handful of questions an
  hour, and new series whenever a process restarts. Plain `increase()` misses a series' first
  event and extrapolates, so one rating showed as 1.48. The flags fix both:
  - `created-timestamp-zero-ingestion` writes a zero at each series' start;
  - `promql-extended-range-selectors` allows `anchored` ranges, which don't extrapolate.

  The dashboard's counts are exact as a result.
- When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, the LLM spans also go to
  Langfuse (Cloud by default; `LANGFUSE_HOST` for another region or a self-hosted instance). A
  span processor passes on only the `pydantic-ai` scope, so Langfuse sees model calls and
  nothing else: no SQL, no HTTP requests.

## Consequences

- Prompts and replies are in the spans, so they contain lecture text and users' questions.
  Locally that stays on the machine. With Langfuse keys set it goes to Langfuse Cloud, which is
  fine for the openly licensed lectures used in development. Private uploads (Phase 6) need a
  self-hosted Langfuse, or no keys.
- The blueprint's queue depth isn't directly visible: the SDK sees how long activities wait
  (schedule-to-start latency), not how many are waiting. The Temporal server's metrics would
  show the backlog; its dev server doesn't export them here.
- The SQLAlchemy instrumentation declares support up to SQLAlchemy 2.0 and silently does
  nothing on 2.1. `telemetry.trace_queries` passes `skip_dep_check`; a unit test checks
  statements still become spans.
- `anchored` is experimental in Prometheus 3.14. The image is pinned, so an upgrade that drops
  or renames it shows up as dashboard errors, not wrong numbers.
- On Windows bind mounts Grafana doesn't notice changes to the dashboard JSON. Restart it to
  load them: `docker compose -f infra/compose.yaml restart otel-lgtm`.
- Phase 6 points `OTEL_ENDPOINT` at a managed backend. `otel-lgtm` keeps its data in a Docker
  volume and has no auth, so it binds to 127.0.0.1 only.
- Sentry (blueprint section 6) isn't added: errors are logged to Loki with their trace ids, and
  failed spans are marked in Tempo. Worth revisiting when there are users to hear from.
