"""lecture-bench: how fast each model runs, before and after, on the same machine (Phase 5c).

    uv run lecture-bench embeddings   # TEI on the CPU vs the GPU
    uv run lecture-bench asr          # faster-whisper at each compute type, GPU and CPU
    uv run lecture-bench detector     # RF-DETR in PyTorch, ONNX Runtime and TensorRT

Results print as a table and are saved to data/bench/. Needs the Compose stack (make up) and
MIT 6.0001 Lecture 10 processed.
"""

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from lecture_bench import asr, detector, embeddings
from lecture_bench.gpu import PeakMemory
from lecture_core.settings import Settings
from lecture_evals.client import find_lecture
from lecture_evals.golden import GoldenSet
from lecture_evals.suites.asr import DEFAULT_CAPTIONS_DIR, CaptionsSet
from lecture_evals.suites.asr import DEFAULT_DATASET as CAPTIONS

GOLDEN = Path("evals/datasets/golden-qa/mit-6.0001-lecture-10.json")
OUT = Path("data/bench")
API = "http://localhost:8000"
# The embedding model as Compose serves it (infra/compose.yaml, and compose.gpu.yaml with a GPU),
# started by the benchmark on ports of its own, so it doesn't matter which one the stack runs.
TEI_IMAGES = {
    "cpu": ("ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4", 8084),
    "gpu": ("ghcr.io/huggingface/text-embeddings-inference:86-1.9.4", 8083),
}
TEI_ARGS = [
    "--model-id",
    "Qwen/Qwen3-Embedding-0.6B",
    "--revision",
    "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    "--max-batch-tokens",
    "4096",
]
TEI_VOLUME = "lecture-summariser_tei-data"
# The detector reported in the README (Phase 5b): RF-DETR Nano at 384 px.
DETECTOR_RUN = Path("data/detector/runs/nano-20260929T123833Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lecture-bench", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("embeddings", help="TEI on the CPU vs the GPU")
    recognition = commands.add_parser("asr", help="faster-whisper compute types")
    recognition.add_argument(
        "--variants",
        default="cuda:float16,cuda:int8_float16,cuda:int8,cpu:int8",
        help="device:compute_type, comma-separated (the pipeline uses cuda:int8_float16)",
    )
    boxes = commands.add_parser("detector", help="RF-DETR in PyTorch, ONNX Runtime, TensorRT")
    boxes.add_argument("--run", type=Path, default=DETECTOR_RUN, help="the trained model's run")
    boxes.add_argument("--dataset", type=Path, default=Path("data/detector/dataset"))
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.command == "detector":
        return _detector(args.run, args.dataset)
    if args.command == "embeddings":
        return _embeddings()
    if args.command == "asr":
        return _asr(args.variants.split(","))
    return 2


def _asr(variants: list[str]) -> int:
    captions = CaptionsSet.load(CAPTIONS)
    audio = asr.extract_audio(
        Path("data/lectures") / captions.lecture.video, OUT / "lecture-10.flac"
    )
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    raw = OUT / f"asr-raw-{stamp}.json"
    asr.run_in_gpu_image(audio, raw, variants)
    results = asr.score_runs(raw, captions, DEFAULT_CAPTIONS_DIR)
    (OUT / f"asr-{stamp}.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2), encoding="utf-8"
    )
    print("| Variant | Load | Lecture 10 (51 min) | Real-time factor | GPU memory | WER | Terms |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        on_gpu = r.gpu_mb is not None and not r.variant.startswith("cpu")
        memory_mb = f"{r.gpu_mb:.0f} MB" if on_gpu else "-"
        print(
            f"| {r.variant} | {r.load_s:.1f} s | {r.seconds:.0f} s | {r.real_time_factor:.3f}"
            f" | {memory_mb} | {r.wer:.1%} | {r.term_recall:.1%} |"
        )
    return 0


def _embeddings() -> int:
    settings = Settings()
    golden = GoldenSet.load(GOLDEN)
    with httpx.Client(base_url=API, timeout=60) as client:
        lecture_id = find_lecture(client, golden.lecture)
    chunks = embeddings.lecture_chunks(settings.qdrant_url, settings.qdrant_collection, lecture_id)

    with _tei_server("cpu") as url:
        tokens = embeddings.token_count(url, [c.text for c in chunks])
        print(f"Lecture 10: {len(chunks)} chunks, {tokens} tokens", flush=True)
        cpu, reference = embeddings.benchmark("TEI, CPU", url, chunks, golden, tokens)
    print(f"CPU: {cpu.embed_s:.0f} s", flush=True)
    with PeakMemory() as memory, _tei_server("gpu") as url:
        gpu, _ = embeddings.benchmark("TEI, GPU", url, chunks, golden, tokens, reference)
    gpu.gpu_mb = memory.added_mb

    results = [cpu, gpu]
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    (OUT / f"embeddings-{stamp}.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2), encoding="utf-8"
    )
    print(
        "| Variant | Lecture 10 embedded | Tokens/s | Question, p50 | Recall@5 | MRR@10 | "
        "nDCG@10 | Cosine to CPU | GPU memory |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        cosine = f"{r.cosine_to_reference:.4f}" if r.cosine_to_reference is not None else "-"
        memory_mb = f"{r.gpu_mb:.0f} MB" if r.gpu_mb is not None else "-"
        print(
            f"| {r.variant} | {r.embed_s:.1f} s | {r.tokens_per_s:.0f} | {r.query_p50_ms:.0f} ms"
            f" | {r.recall_at_5:.2f} | {r.mrr_at_10:.2f} | {r.ndcg_at_10:.2f} | {cosine}"
            f" | {memory_mb} |"
        )
    return 0


def _detector(run: Path, dataset: Path) -> int:
    checkpoint = run / "checkpoint_best_total.pth"
    frames, coco = detector.test_frames(dataset / "test")
    calibration, _ = detector.test_frames(dataset / "train")
    fp32 = detector.export_onnx(checkpoint, OUT / "detector")
    fp16 = detector.to_fp16(fp32)
    dynamic = detector.to_int8_dynamic(fp32)
    calibrated = detector.to_int8_calibrated(fp32, [f.image for f in calibration[::5]])
    # Built one at a time, so a variant that fails is reported and the others still run.
    variants: list[tuple[str, str, Callable[[], detector.Runner]]] = [
        ("PyTorch fp32", "cpu", lambda: detector.torch_runner(checkpoint, "cpu", half=False)),
        ("ONNX Runtime fp32", "cpu", lambda: detector.onnx_runner(fp32)),
        ("ONNX Runtime int8, dynamic", "cpu", lambda: detector.onnx_runner(dynamic)),
        ("ONNX Runtime int8, calibrated", "cpu", lambda: detector.onnx_runner(calibrated)),
        ("PyTorch fp32", "gpu", lambda: detector.torch_runner(checkpoint, "cuda", half=False)),
        ("PyTorch fp16", "gpu", lambda: detector.torch_runner(checkpoint, "cuda", half=True)),
        ("TensorRT fp32", "gpu", lambda: detector.tensorrt_runner(fp32)),
        ("TensorRT fp16", "gpu", lambda: detector.tensorrt_runner(fp16)),
        ("TensorRT int8, calibrated", "gpu", lambda: detector.tensorrt_runner(calibrated)),
    ]
    results = []
    failed = []
    for name, device, build in variants:
        try:
            results.append(detector.evaluate(name, device, build(), frames, coco))
        except Exception as error:
            failed.append(f"{name} ({device}): {error}")
            print(f"{name} ({device}) failed: {error}", flush=True)
            continue
        print(f"{name} ({device}): {results[-1].ms_p50:.1f} ms", flush=True)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    (OUT / f"detector-{stamp}.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2), encoding="utf-8"
    )
    for failure in failed:
        print(f"Failed: {failure}")
    if not results:
        return 1
    classes = list(results[0].ap_by_class)
    print(
        "| Variant | Device | ms per frame (p50) | mAP50:95 | mAP50 | " + " | ".join(classes) + " |"
    )
    print("|---" * (5 + len(classes)) + "|")
    for r in results:
        print(
            f"| {r.variant} | {r.device} | {r.ms_p50:.1f} | {r.map_50_95:.3f} | {r.map_50:.3f} | "
            + " | ".join(f"{r.ap_by_class[c]:.2f}" for c in classes)
            + " |"
        )
    return 1 if failed else 0


@contextmanager
def _tei_server(device: str) -> Iterator[str]:
    """The embedding model on its own TEI server, for as long as the block runs."""
    image, port = TEI_IMAGES[device]
    name = f"lecture-bench-embeddings-{device}"
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - docker from PATH
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            *(["--gpus", "all"] if device == "gpu" else []),
            "-p",
            f"127.0.0.1:{port}:80",
            "-v",
            f"{TEI_VOLUME}:/data",
            image,
            *TEI_ARGS,
        ],
        check=True,
        capture_output=True,
    )
    try:
        url = f"http://localhost:{port}"
        _wait_healthy(url)
        yield url
    finally:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "stop", name],  # noqa: S607 - docker from PATH
            check=False,
            capture_output=True,
        )


def _wait_healthy(url: str, timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError(f"{url} didn't become healthy in {timeout_s:.0f} s")


if __name__ == "__main__":
    sys.exit(main())
