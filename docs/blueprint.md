# Lecture summariser v2: architecture blueprint

Rebuild of the Multimodal Educational Video Summarisation System as a production-style, end-to-end system.
Written 26 Sep 2026. Model and tool choices reflect what was current then, so re-check versions before locking them.

---

## 1. What v2 does

### Core features
- Upload a lecture video (resumable, straight to storage) or ingest a URL from an openly licensed source.
- Live processing progress. Any stage can be re-run with a new model or prompt version without redoing the other stages.
- Lecture page: streaming video player, chapter list, a transcript that follows playback (click a line to jump there), and a strip of slide images.
- Study outputs: a TL;DR, chapter summaries, key concepts and definitions, formulas (LaTeX), and quiz questions. Every item cites a timestamp, a slide, or both.
- Q&A chat for one lecture or a whole course: hybrid retrieval, reranking, and a streamed answer with clickable [mm:ss] citations. When the lecture doesn't cover the question, the answer says so.
- Feedback (thumbs up/down plus a reason) is stored and turned into eval cases.
- Deleting a lecture removes the video, artifacts, database rows and vectors.

### Non-functional targets (fill in real numbers after the first baseline run)
- Processing time per lecture-hour, measured on the dev machine and on the cloud profile.
- Q&A p95 time-to-first-token.
- Cost per lecture-hour (API tokens plus GPU seconds), recorded on every run.
- Quality gates: WER, slide-boundary F1, retrieval Recall@5, answer faithfulness, citation accuracy.
- Every stage is idempotent and safe to retry, so a retry never repeats finished work.

### Constraints
- Dev machine: Windows with a **6GB NVIDIA GPU**. Develop inside WSL2. Only small, quantised models run locally, and only one at a time.
- Near-zero idle cost: scale-to-zero GPU and free tiers where possible.
- Solo build, so ship in vertical slices and always keep a working demo.

---

## 2. What changes from v1

| v1 | v2 | Why |
|---|---|---|
| YOLOv8 | **YOLO26** (released Jan 2026), fine-tuned on lecture frames: slide region, presenter, whiteboard, equation, code, chart, table | NMS-free export. The detector also gets a concrete job: crop the slide and mask the presenter so slide-change detection is reliable, and route each slide to the right extractor |
| Whisper | **faster-whisper large-v3-turbo** (int8) + Silero VAD + word timestamps, benchmarked against **Qwen3-ASR** | Fits in 6GB and gives word-level times for citations. Whisper has been overtaken on accuracy leaderboards, so choose using your own WER and speed numbers |
| BLIP-2 captions | **OCR** for text + a **vision LLM** for figures, equations and charts, returning structured JSON | BLIP-2 captions are generic ("a slide with text"). Current vision LLMs read slide text, equations and charts |
| "Transformer-based summaries" | Hierarchical, timestamp-grounded summaries with schema-validated output and a faithfulness check | Summaries you can verify and click through |
| RAG with vector embeddings | Hybrid dense + BM25 search in **Qdrant**, a reranker, and citations to timestamps and slides | Better recall on technical terms, and answers can be checked |
| ONNX + quantisation | ONNX/TensorRT for the detector, CTranslate2 int8 for ASR, AWQ/FP8 on vLLM for self-hosted LLMs, all benchmarked | Real before/after numbers |
| Modular pipeline | **Temporal** workflows + a content-addressed stage cache, Docker, CI/CD, IaC, tracing, and evals in CI | What "production" actually means |

---

## 3. Architecture

```
 Next.js web app ──► FastAPI (REST + SSE, auth) ──► Temporal (one workflow per lecture)
       │ presigned upload                                │ task queues: cpu | gpu (concurrency 1) | llm
       ▼                                                 ▼
 Object storage ◄──── CPU worker : FFmpeg, slide changes, OCR, fusion, LLM calls, indexing
 (SeaweedFS / S3/R2)   GPU worker : ASR, detector ......................... local 6GB GPU  or  Modal
                       Remote     : vision LLM + LLM ..................... hosted API     or  vLLM on Modal
                       TEI        : embeddings + reranker ................ CPU locally, GPU in cloud
 Postgres (metadata, timeline, summaries)        Qdrant (hybrid vectors)
 Ops: OpenTelemetry → Grafana · Langfuse (LLM traces, evals) · Sentry · MLflow + DVC
```

Principles:
- **The API is stateless and never processes video.** It issues presigned upload URLs, starts workflows, serves results, and runs the online Q&A path.
- **Workers are thin.** They register Temporal activities that call plain functions in `packages/`, which are easy to unit-test.
- **Models sit behind interfaces** (`Transcriber`, `Detector`, `OCR`, `SlideReader`, `LLM`) and are selected by config, so switching between local, API and Modal backends needs no code changes.
- **The lecture timeline is the product.** Summaries, search and the UI all read from one time-aligned data model.

---

## 4. Processing pipeline (Temporal workflow `ProcessLecture`)

1. **Ingest** (CPU): check the file with ffprobe (codec, duration and size limits), compute a SHA-256 content hash (for dedupe), transcode to HLS for playback, extract 16 kHz mono audio, and sample frames at about 1 fps.
2. **Speech track** (GPU): Silero VAD → faster-whisper (batched, word timestamps) → sentence segments.
   *Experiment:* feed slide vocabulary to ASR as `hotwords` or an initial prompt, then measure the WER change on technical terms.
3. **Visual track** (GPU + CPU):
   - Run YOLO26 on the sampled frames to find the slide or board region, the presenter, and content boxes.
   - Detect slide changes on the cropped region with the presenter masked, using a perceptual hash plus SSIM. Keep the last frame of each stable interval so slides that build up bullet by bullet are handled.
   - Run OCR (RapidOCR) on each unique slide.
   - **Routing:** slides with figures, equations, charts or tables (according to the detector), or with low OCR confidence, go to the vision LLM, which returns `{title, text, figure_description, latex, code}`. Plain text slides stop at OCR.
4. **Fuse** (CPU): build the lecture timeline. Each segment is aligned to a slide interval and holds the transcript, slide text, visual notes, start and end. Chapters come from slide boundaries plus embedding similarity, and an LLM writes the chapter titles.
5. **Generate** (LLM): map step (chapter summaries) → reduce step (TL;DR, concepts, glossary, quiz). Everything is Pydantic-validated JSON that cites segment IDs. A verification pass flags or removes claims the cited segments don't support.
6. **Index**: one chunk per segment (about 30–90 s of speech plus slide text). Dense (Qwen3-Embedding-0.6B) and sparse (BM25) vectors go into Qdrant with payload `lecture_id, course_id, start, end, slide_id, chapter_id`.
7. **Finalise**: write stage timings, GPU-seconds, tokens and cost to `pipeline_runs`, and notify the UI over SSE.

**Stage cache.** Every stage output is stored as an artifact with key `hash(input artifact keys + stage name + stage version + model id + params)`. The workflow checks the cache before running a stage, so changing the summary prompt re-runs only steps 5–7. This keeps experiments reproducible and cheap.

**Failure handling.** Each activity has a timeout and heartbeats during long GPU work, and retries use exponential backoff. Bad input raises non-retryable errors. LLM calls run on their own queue with rate-limit handling. A workflow signal cancels a run.

---

## 5. Q&A path (online, outside the workflow)

Query → optional rewrite using chat history → hybrid search in Qdrant (filtered to the lecture or course, fused with RRF) → rerank the top ~30 → take the top ~6 segments → LLM answer with [mm:ss] citations, streamed over SSE → check that each citation points to retrieved context → store the thread and feedback.

Treat retrieved transcript and slide text as **untrusted input**. Keep it inside delimited context blocks and never follow instructions found in it, since a slide can contain a prompt-injection string.

---

## 6. Tech stack

### AI / ML
| Job | Pick | Notes and alternatives |
|---|---|---|
| Speech-to-text | faster-whisper `large-v3-turbo`, int8 | Benchmark it against Qwen3-ASR-0.6B/1.7B (Apache-2.0, 52 languages, timestamps via Qwen3-ForcedAligner). Parakeet and Canary are strong on English but licensed CC-BY-4.0 |
| VAD | Silero VAD (built into faster-whisper) | Skips silence and cuts hallucinated text |
| Detection | YOLO26 n/s (Ultralytics), fine-tuned | AGPL-3.0, which is fine for an open-source repo but a company would need a licence. RF-DETR (Apache-2.0 for the standard sizes) is the alternative |
| Labelling | Label Studio (or CVAT) | Pre-label with a base model, then correct by hand |
| OCR | RapidOCR (PP-OCRv5 models on ONNX Runtime) | No PaddlePaddle install, fast on CPU, has English/Latin models |
| Slide understanding | Vision LLM through an API: Gemini Flash-Lite on the free tier for development with public lectures, Claude Haiku 4.5 on paid runs | Self-hosted: Qwen3-VL-8B or Qwen3.5-9B on vLLM (Modal). Offline dev: Qwen3.5-4B quantised in Ollama |
| Summaries + Q&A | Claude Haiku 4.5 or Gemini Flash, called through **Pydantic AI** | Provider-agnostic, schema-validated output, fallback models, OpenTelemetry traces |
| Embeddings | Qwen3-Embedding-0.6B (Apache-2.0, 32K context), served by Hugging Face TEI | EmbeddingGemma-300M if CPU-bound (2K context) |
| Sparse | BM25 (FastEmbed `Qdrant/bm25`) | Catches exact technical terms |
| Reranker | bge-reranker-v2-m3 (TEI) | Qwen3-Reranker-0.6B (vLLM) |
| Multimodal search (stretch) | Qwen3-VL-Embedding-2B (Apache-2.0) | Search slides by what they show |

### Backend and data
- Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic, SSE.
- Temporal (Python SDK) for orchestration, with `temporal server start-dev` locally.
- Postgres for metadata, the timeline (JSONB) and summaries. Qdrant for vectors.
- Object storage: SeaweedFS locally (Apache-2.0), S3 or Cloudflare R2 in the cloud. *MinIO's open-source edition is no longer maintained; its repo was archived in April 2026.*
- FFmpeg through PyAV or a subprocess, with HLS output.

### Frontend
- Next.js + TypeScript, Tailwind + shadcn/ui, TanStack Query, hls.js player, Uppy for multipart uploads, and a typed client generated from FastAPI's OpenAPI schema.

### MLOps / LLMOps
- **MLflow**: detector training runs, ASR benchmarks and eval runs, plus a model registry for detector versions.
- **DVC**: datasets (annotated frames, golden sets) stored on an S3 or R2 remote.
- **Langfuse**: LLM traces, prompt versions, and eval datasets and scores. It joined ClickHouse in early 2026 and remains open source and self-hostable, and the cloud free tier is enough for a solo project.
- Prompts are versioned in git (`prompts/`), and every output records its prompt version and model.

### Observability
- The OpenTelemetry SDK in the API and workers sends to `grafana/otel-lgtm` locally (Grafana, Tempo, Loki and Prometheus in one container).
- Sentry for errors.
- Metrics: stage durations, queue depth, GPU memory, tokens and cost, cache hit rate, Q&A time-to-first-token, feedback rate.

### Engineering and delivery
- uv workspaces (Python monorepo), pnpm (web), Ruff, mypy (or pyright), pre-commit.
- pytest, testcontainers (Postgres, Qdrant, S3), Playwright e2e tests, and optionally schemathesis against the OpenAPI spec.
- Multi-stage Docker images, with a CUDA base image for the GPU worker. Compose profiles: `gpu`, `observability`, `offline-llm`.
- GitHub Actions: lint → type-check → tests → build → image scan → push to GHCR. A separate eval workflow runs on PRs that touch `prompts/`, model config or retrieval. Deploy on tag.
- Terraform for cloud resources, Modal for GPU functions.
- **Supply-chain hygiene** (a live topic in 2026):
  - Keep a lockfile with hashes (`uv.lock`), and use Renovate or Dependabot plus pip-audit.
  - **Pin every GitHub Action to a full commit SHA.**
  - Why: in March 2026 the TeamPCP campaign force-pushed malicious `trivy-action` tags and shipped a bad Trivy release (v0.69.4). It then published credential-stealing LiteLLM 1.82.7/1.82.8 to PyPI on 24 Mar 2026; LiteLLM 1.83.0+ is the clean line.

---

## 7. Running on a 6GB GPU

| Model | Where it runs | Rough VRAM (measure it) |
|---|---|---|
| faster-whisper large-v3-turbo, int8 | local GPU | ~2–3 GB with batching |
| YOLO26 n/s (ONNX/TensorRT FP16) | local GPU | < 1 GB |
| RapidOCR | CPU | – |
| Qwen3-Embedding-0.6B + reranker (TEI) | CPU locally | – |
| Vision LLM + LLM | remote (API or Modal) | – |
| Qwen3.5-4B Q4 (offline mode only) | local GPU, while ASR is unloaded | ~3 GB + KV cache |

- Run one GPU worker with Temporal `max_concurrent_activities=1` on the `gpu` queue. Models load lazily and unload between stages when needed. Windows also keeps some VRAM for the display.
- Detector fine-tuning fits in 6GB with a small YOLO26 model and batch size ~8 (or AutoBatch). For bigger sweeps, use Kaggle or Colab free GPUs and log the runs to MLflow.
- The cloud profile uses the same activity interface with backend = Modal (L4 or A10). Modal's Starter plan includes $30/month of compute, and an L4 costs about $0.80/hour, which is plenty for development bursts.
- **Privacy:** Google may use content sent to Gemini's free tier to improve its products. Use the free tier only for public, openly licensed lectures, and a paid tier for anything private.

**Rough cost** (an estimate to replace with measured numbers). With Claude Haiku 4.5 at $1/$5 per million input/output tokens, one lecture-hour costs roughly **$0.20–0.30** in API calls. That assumes about 30 slides routed to the vision LLM (~1.9k input and ~350 output tokens each) and map-reduce summaries plus a verification pass (~60k input and ~11k output tokens). ASR on a Modal L4 adds a few cents.

---

## 8. Data model (Postgres)

- `users`, `courses`, `lectures` (status, duration, content hash, storage keys, licence/attribution)
- `pipeline_runs` (workflow id, status, stage versions, timings, GPU-seconds, tokens, cost)
- `stage_artifacts` (cache key, stage, version, model, params, storage key, created_at)
- `transcript_segments` (start, end, text, words JSONB, speaker)
- `slides` (start, end, image key, OCR text, VLM JSON, detections JSONB)
- `timeline_segments` (the fused unit: start, end, transcript, slide_id, visual notes, chapter_id)
- `chapters`, `summaries` (type, JSON content, citations, prompt version, model)
- `qa_threads`, `qa_messages` (citations JSONB, latency, tokens), `feedback`
- `eval_runs` (suite, dataset version, metrics JSONB, git sha)

Qdrant collection `segments` holds dense and sparse vectors with payload `lecture_id, course_id, start, end, slide_id, chapter_id`. An optional `slides_visual` collection is for the stretch goal.

Storage layout: `raw/{lecture}/source.*`, `media/{lecture}/hls/…`, `media/{lecture}/audio.flac`, `frames/{lecture}/…`, `slides/{lecture}/{n}.jpg`, `artifacts/{stage}/{cache_key}.json`.

---

## 9. API (FastAPI, `/v1`)

- `POST /lectures` creates a lecture and returns a presigned multipart upload
- `POST /lectures/{id}/process` (with an `Idempotency-Key` header) starts the workflow
- `GET /lectures/{id}` and `GET /lectures/{id}/events` (SSE progress)
- `GET /lectures/{id}/timeline | summary | chapters | slides | transcript`
- `POST /lectures/{id}/ask` and `POST /courses/{id}/ask` stream the answer over SSE
- `GET /search?q=` searches across lectures
- `POST /feedback`
- `POST /lectures/{id}/stages/{stage}:rerun` (admin)
- `DELETE /lectures/{id}` purges the lecture everywhere
- `GET /healthz`, `GET /readyz`

Auth: an OIDC provider issues a JWT that FastAPI verifies. Each user has quotas and rate limits on processing and Q&A.

---

## 10. Repo structure

```
lecture-summariser/
├── apps/
│   ├── api/                  # FastAPI: routes, auth, SSE, presigned uploads
│   └── web/                  # Next.js + TypeScript UI
├── workers/
│   ├── cpu/                  # Temporal worker: media, OCR, fusion, LLM calls, indexing
│   └── gpu/                  # Temporal worker: ASR + detector (concurrency 1)
├── packages/                 # shared Python code (uv workspace members)
│   ├── core/                 # domain models, settings, DB + storage clients
│   ├── pipeline/             # workflows, activities, stage cache
│   ├── perception/           # Transcriber / Detector / OCR / SlideReader adapters
│   ├── rag/                  # chunking, hybrid search, rerank, cited answers
│   └── llm/                  # Pydantic AI agents, output schemas, cost tracking
├── prompts/                  # versioned prompt templates
├── ml/
│   ├── detector/             # dataset prep, YOLO26 training, ONNX/TensorRT export
│   ├── asr_bench/            # WER + speed benchmarks across ASR models
│   └── optimisation/         # quantisation + latency benchmarks, results tables
├── evals/
│   ├── datasets/             # golden Q&A, slide boundaries, references (DVC-tracked)
│   └── suites/               # wer, boundaries, retrieval, faithfulness, citations
├── infra/
│   ├── docker/               # Dockerfiles: api, cpu-worker, gpu-worker (CUDA), web
│   ├── compose.yaml          # local stack + profiles
│   ├── modal/                # serverless GPU functions (cloud profile)
│   └── terraform/            # cloud resources
├── tests/                    # unit, integration (testcontainers), e2e (Playwright)
├── docs/                     # architecture.md, adr/, runbooks/
├── .github/workflows/        # ci.yml, evals.yml, deploy.yml
├── pyproject.toml            # uv workspace root
└── Makefile
```

Suggested first ADRs: Temporal vs Celery, Qdrant vs pgvector, hosted vs self-hosted models, YOLO26 vs RF-DETR (licence), and the stage-cache design.

---

## 11. Evaluation plan

### Data
- **15–30 MIT OpenCourseWare lectures** (CC BY-NC-SA 4.0: attribute MIT and the faculty, non-commercial, share-alike). They come with human captions (ground truth for ASR) and often slide PDFs (ground truth for OCR and slide boundaries). Download from OCW or the Internet Archive rather than scraping YouTube.
- **AVLectures** (WACV 2023, built from MIT OCW lectures, with segmentation labels) for checking chapter segmentation.
- **Golden Q&A set:** about 150 questions, each with the timestamp span that answers it. Seed with LLM-generated questions, then edit and check them all by hand.
- **Detector data:** 500–1,000 annotated frames. Split train/val/test **by lecture, not by frame**, so frames from one lecture don't leak across splits.

### Metrics
| Component | Metric |
|---|---|
| ASR | WER overall and on a technical-term list; real-time factor |
| Detector | mAP50-95 per class |
| Slide changes | boundary precision/recall (±2 s) |
| OCR | CER against slide PDF text |
| Retrieval | Recall@5, MRR, nDCG@10 on the golden Q&A set |
| Answers | faithfulness and correctness (an LLM judge calibrated on ~50 hand-graded answers), citation accuracy |
| Summaries | coverage and faithfulness (LLM judge with a rubric), plus human spot-checks |
| System | minutes per lecture-hour, Q&A p95 time-to-first-token, $ per lecture-hour |

**Baseline worth running.** Send the whole video to a long-context multimodal model (e.g., Gemini) in one call, then compare summary quality, citation accuracy, cost and latency with your pipeline. That answers the obvious interview question, "why not just use Gemini?", with data.

**CI gate.** A small, fast eval subset runs on PRs that touch prompts, models or retrieval, and the build fails if a metric drops past its threshold. The full suite runs nightly or on demand, with results logged to MLflow and Langfuse.

---

## 12. Deployment

- **Local** (main dev environment and demo): Docker Compose inside WSL2 with GPU passthrough.
- **Cloud demo** (cheap):
  - The web app runs on Vercel.
  - The API, CPU worker, Temporal, Postgres and Qdrant run as containers on one small VM provisioned by Terraform (or on managed free tiers for Postgres and Qdrant).
  - GPU stages run on Modal, and media sits on S3 or R2 behind a CDN.
  - The public demo shows pre-processed OCW lectures with live Q&A. Uploads sit behind auth and quotas.
- **Scale-out path** (write it as an ADR, build it only if you have time): ECS/EKS or Kubernetes (a Helm chart, tested on local k3d), managed Postgres, Temporal Cloud, and GPU workers that autoscale on queue backlog.

---

## 13. Build phases (part-time, roughly 2 weeks each; adjust to your schedule)

1. **Foundation:** repo, uv workspace, Compose (Postgres, Qdrant, SeaweedFS, Temporal), FastAPI skeleton, CI, first ADRs.
2. **Vertical slice:** upload → HLS + audio → ASR → hash-based slide changes → vision LLM on every slide → timeline → chapter summaries → a lecture page with player, chapters and transcript. *First demo.*
3. **RAG Q&A:** chunking, hybrid search, reranker, streamed cited answers, course-level search.
4. **Evals + observability:** golden set, eval suites, Langfuse + OpenTelemetry, cost tracking, a CI eval gate, and the single-call Gemini baseline.
5. **CV + optimisation:** annotate frames, fine-tune YOLO26, add detector-based cropping and OCR-vs-VLM routing, and benchmark ONNX/TensorRT/int8 with before/after tables.
6. **Ship:** auth, quotas, Terraform + Modal deploy, CD, and a README with the diagram, demo video and results.

Stretch goals: an MCP server that exposes lecture search to AI assistants, multimodal slide search, diarisation for Q&A segments, multilingual summaries, Markdown/Anki export, Helm on k3d.

---

## 14. Numbers to collect (for the README and CV)
- WER (overall and on technical terms) for each ASR candidate; real-time factor on the 6GB card vs a Modal L4.
- Detector mAP; slide-boundary F1 with and without the detector crop.
- Share of slides sent to the vision LLM with and without routing, the cost and time saved, and any quality change.
- Retrieval Recall@5 for dense-only vs hybrid vs hybrid + rerank.
- Answer faithfulness and citation accuracy.
- Latency before and after ONNX/TensorRT/int8 for each model, on the same hardware.
- Minutes and dollars per lecture-hour; Q&A p95 time-to-first-token.
- The pipeline vs the single-call Gemini baseline.

---

## Sources
- [Ultralytics YOLO26 docs](https://docs.ultralytics.com/models/yolo26)
- [Best open ASR models in 2026 (MarkTechPost, Jul 2026)](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- [Qwen3-ASR (GitHub)](https://github.com/QwenLM/Qwen3-ASR)
- [Qwen3.5 small models (MarkTechPost, Mar 2026)](https://www.marktechpost.com/2026/03/02/alibaba-just-released-qwen-3-5-small-models-a-family-of-0-8b-to-9b-parameters-built-for-on-device-applications/) · [Qwen3.5-4B model card](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Qwen3-VL-Embedding (GitHub)](https://github.com/QwenLM/Qwen3-VL-Embedding)
- [Open-source embedding and reranker models 2026](https://builderai.tools/blog/best-open-source-embedding-models-2026)
- [Hugging Face Text Embeddings Inference](https://github.com/huggingface/text-embeddings-inference)
- [RapidOCR model list](https://rapidai.github.io/RapidOCRDocs/main/model_list/)
- [RF-DETR licensing change (GitHub issue)](https://github.com/roboflow/rf-detr/issues/592)
- [MinIO community edition archived](https://www.cloudhim.com/cloud-infrastructure/minio-community-edition-archived-what-to-do)
- [Langfuse joins ClickHouse](https://langfuse.com/blog/joining-clickhouse)
- [LiteLLM security update, March 2026](https://docs.litellm.ai/blog/security-update-march-2026)
- [Trivy compromised by TeamPCP (Wiz)](https://www.wiz.io/blog/trivy-compromised-teampcp-supply-chain-attack)
- [Modal pricing](https://modal.com/pricing)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Claude Haiku 4.5](https://www.anthropic.com/claude/haiku)
- [Pydantic AI model providers](https://ai.pydantic.dev/models/overview/)
- [grafana/docker-otel-lgtm](https://github.com/grafana/docker-otel-lgtm)
- [MIT OpenCourseWare terms of use](https://mitocw.zendesk.com/hc/en-us/articles/4414774353051-What-are-the-requirements-of-use-for-MIT-OpenCourseWare)
- [AVLectures dataset (GitHub)](https://github.com/Darshansingh11/AVLectures)
