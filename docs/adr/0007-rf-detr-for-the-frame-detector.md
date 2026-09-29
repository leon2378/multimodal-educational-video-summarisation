# 0007: RF-DETR for the frame detector

- Status: Accepted
- Date: 2026-09-29
- Code: Phase 5b (to come)

## Context

Phase 5b adds a detector for the sampled video frames (blueprint sections 4 and 6): the slide,
the presenter, and what on a slide the vision LLM should read (figures, tables, annotations). It
replaces two guesses: the brightness test that tells slides from camera shots, and the ink
measure that routes slides to the vision LLM ([architecture](../architecture.md#ocr-and-routing-phase-5a)).

It will be fine-tuned on a few hundred frames labelled without drawing boxes by hand: slide
boxes from matching frames to the lecture's slide PDF, people from a detector pretrained on
COCO, annotations from where a frame differs from its PDF page. It runs on the GPU worker, about
3,000 frames for a 51-minute lecture at 1 fps, on a 6 GB card it shares with speech recognition
and the reranker. Phase 5c then benchmarks it exported to ONNX and TensorRT, and quantised.

## Options

- **YOLO26 (Ultralytics), n or s.** AGPL-3.0, or Ultralytics' paid enterprise licence. COCO
  mAP50-95 40.9 (n) and 48.6 (s); 38.9 and 87.2 ms an image on the CPU with ONNX. The most
  polished training and export (ONNX, TensorRT, OpenVINO and more) and easy on 6 GB. But AGPL
  would settle this repo's licence, which it doesn't have yet, and anyone reusing the code
  commercially would need Ultralytics' licence.
- **RF-DETR (Roboflow), Nano or Small.** Apache-2.0 for Nano to Large; XL and 2XL are under
  Roboflow's own licence and not used. A transformer detector on a pretrained DINOv2 backbone,
  designed to fine-tune on small datasets from domains far from COCO, as lecture frames are.
  Needs PyTorch (a 2 GB CUDA build on Windows) and transformers, and exports to ONNX. Its docs
  don't state the memory training needs, and int8 is less routine for a transformer detector.

## Decision

RF-DETR, Nano first, Small if Nano's accuracy falls short; `rfdetr` 1.11 for training. PyTorch
stays out of the pipeline: training runs on the host GPU, and the worker is meant to run the
exported model with ONNX Runtime.

## Consequences

- The repo's licence stays a free choice for Phase 6.
- If RF-DETR doesn't train in 6 GB, or its accuracy or export falls short, the fallback is
  YOLO26. The labels will be in COCO format, which both train from, so only the training step
  changes; a new ADR would supersede this one.
- PyTorch is a training-only dependency, about 2 GB to download.
- Accuracy (mAP per class) is measured against the automatic labels unless a test set is
  checked by hand; the results say which.
