# 0008: Where the self-hosted models run, from the Phase 5c benchmarks

- Status: Accepted
- Date: 2026-09-30
- Code: `infra/compose.gpu.yaml`, `ml/bench`

## Context

The stack runs three models itself: speech recognition (faster-whisper large-v3-turbo) on the
GPU worker, the embedding model (Qwen3-Embedding-0.6B) and the reranker on Text Embeddings
Inference (TEI), and, once it's in the pipeline, the frame detector (RF-DETR Nano,
[ADR 0007](0007-rf-detr-for-the-frame-detector.md)). The blueprint (section 7) put the
embedding model and the reranker on the CPU locally and the detector on the GPU in TensorRT
fp16, and asked for before-and-after numbers for ONNX, TensorRT and int8 (section 14). The
reranker had already moved to the GPU: on the CPU it took 77 seconds a query
([ADR 0005](0005-qdrant-for-hybrid-search.md)). The GPU is an RTX 3060 Laptop with 6 GB.

Phase 5c measured each model every way it could run, on that laptop, and scored every variant
(`lecture-bench`; the tables are in the README):

- **Embedding model**, Lecture 10's 47 chunks: 218 s on the CPU (fp32), 1.4 s on the GPU
  (fp16), with the same vectors (mean cosine 1.0000) and search scores. A question: 437 ms
  against 16 ms. 1.5 GB of VRAM. And TEI's CPU server sometimes returns a wrong vector when
  requests overlap (3 of 12 bursts of overlapping requests); the GPU server didn't in 60.
- **Speech recognition**, Lecture 10 (51 minutes): float16 44 s, WER 2.8%, 3.3 GB of VRAM;
  int8_float16 40 s, WER 3.0%, 2.2 GB; int8 about the same as int8_float16; the CPU (int8) 16
  minutes.
- **Frame detector**, a frame: PyTorch 17.6 ms on the GPU, TensorRT fp16 3.8 ms at the same
  mAP. Calibrated int8 was slower than fp32 in TensorRT and lost 0.08 mAP. On the CPU, ONNX
  Runtime 122 ms in fp32 and 69 ms in dynamic int8, which lost 0.004.

## Decision

- **The embedding model runs on the GPU when there is one**: `infra/compose.gpu.yaml` swaps
  the embedding server's image for TEI's GPU one, and the Makefile adds the file when
  `nvidia-smi` finds a GPU (`make up GPU=` leaves it out). Without a GPU it runs on the CPU as
  before. This supersedes ADR 0005 on where the embedding model runs.
- **Speech recognition stays at int8_float16**: as fast as float16 and a third less VRAM, for
  0.2 points of WER.
- **When the detector joins the pipeline**, the GPU worker runs it as a TensorRT fp16 engine,
  built from the ONNX file on the machine that runs it (an engine is tied to its GPU and
  TensorRT version). Without a GPU, ONNX Runtime runs the dynamic int8 file. PyTorch stays out
  of the pipeline, as ADR 0007 has it.

## Consequences

- Embedding stops being the slowest stage: Lecture 10's chunks take 1.4 s instead of 3.5
  minutes. Every search that embeds the question is about 0.4 s faster: through the API, 0.43 s
  with reranking instead of 0.86, 30 to 40 ms without.
- The GPU holds all three at once: 5.1 GB of 6 at speech recognition's peak, with the reranker
  and the embedding model loaded. float16 speech recognition would need 1.1 GB more, which
  doesn't fit.
- The vectors are the same on both, so the embed stage's cache key doesn't change: lectures
  embedded on the CPU stay in the index as they are, and moving between the two re-embeds
  nothing.
- Without a GPU the CPU server's bug remains: a question asked while a lecture is being
  embedded, or a lecture embedded while questions are asked, can get wrong vectors. Serving one
  input at a time (`--max-batch-requests 1`) didn't prevent it. The fix belongs in TEI; until
  then, the GPU is the reliable way to run it.
- TEI's GPU image is built for one compute capability: 8.6, the RTX 30 series, as the
  reranker's already is. Another card needs another tag in both.
- TensorRT is 2.3 GB of libraries, and building an engine takes about a minute, once per
  machine.
- int8 on the GPU is left for later: ONNX Runtime's quantizer makes a model TensorRT runs
  slower than fp32, and making the rest of it fp16 broke its accuracy. NVIDIA's own quantizer
  (ModelOpt) is the next thing to try, if the detector ever needs more than fp16 gives.
