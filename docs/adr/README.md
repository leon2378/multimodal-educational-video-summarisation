# Architecture decision records

One file per decision: context, the options considered, what was chosen, and what it costs.
Add the next one as `NNNN-short-title.md`. When a decision is reversed, write a new ADR and mark the
old one "Superseded by NNNN" instead of editing it.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-stage-cache.md) | Content-addressed stage cache in object storage | Accepted |
| [0002](0002-temporal-for-orchestration.md) | Temporal for pipeline orchestration, from Phase 2 | Accepted |
| [0003](0003-seaweedfs-for-local-object-storage.md) | SeaweedFS as the local S3-compatible store | Accepted |
| [0004](0004-hosted-llms-through-pydantic-ai.md) | Hosted LLMs through Pydantic AI, Gemini's free tier for development | Accepted |
| [0005](0005-qdrant-for-hybrid-search.md) | Qdrant for hybrid search, next to Postgres | Accepted |
| [0006](0006-opentelemetry-to-grafana-and-langfuse.md) | OpenTelemetry into a local Grafana stack, LLM calls to Langfuse | Accepted |

## Still to write

| Decision | Write it by |
|---|---|
| YOLO26 (AGPL-3.0) vs RF-DETR (Apache-2.0) | Phase 5 |
| Scale-out path: Kubernetes, Temporal Cloud, autoscaled GPU workers | Phase 6 |
