"""Speech recognition: faster-whisper large-v3-turbo at each CTranslate2 compute type.

The recognition runs in the GPU worker image (lecture_bench.asr_worker), which has CUDA and
faster-whisper; this side extracts the audio, starts it, and scores each transcript against the
lecture's captions as the ASR eval does (word error rate, technical-term recall).
"""

import json
import subprocess
from pathlib import Path

from pydantic import BaseModel

from lecture_evals.suites.asr import CaptionsSet, score
from lecture_perception import media

WORKER = Path(__file__).with_name("asr_worker.py")


class AsrResult(BaseModel):
    variant: str
    load_s: float
    seconds: float
    real_time_factor: float
    # GPU memory the recognition added at its peak, over what was in use before it loaded.
    gpu_mb: float | None
    wer: float
    term_recall: float


def extract_audio(video: Path, out: Path) -> Path:
    """16 kHz mono FLAC, as the pipeline's audio stage makes it; kept for the next run."""
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(media.extract_audio(video, 16_000))
    return out


def run_in_gpu_image(audio: Path, raw: Path, variants: list[str]) -> None:
    """`audio` and `raw` are under data/, which the image mounts at /app/data."""
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - docker from PATH
            "docker",
            "compose",
            "-f",
            "infra/compose.yaml",
            "--profile",
            "gpu",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "-v",
            f"{WORKER.parent.resolve().as_posix()}:/bench:ro",
            "pipeline",
            "/bench/asr_worker.py",
            f"/app/{audio.as_posix()}",
            f"/app/{raw.as_posix()}",
            *variants,
        ],
        check=True,
    )


def score_runs(raw: Path, captions: CaptionsSet, captions_dir: Path) -> list[AsrResult]:
    cues = captions.read_captions(captions_dir)
    results = []
    for run in json.loads(raw.read_text(encoding="utf-8")):
        scored = score(run["text"], cues, captions.terms)
        before, peak = run["gpu_mb_before"], run["gpu_mb_peak"]
        results.append(
            AsrResult(
                variant=run["variant"],
                load_s=run["load_s"],
                seconds=run["seconds"],
                real_time_factor=run["seconds"] / run["audio_s"],
                gpu_mb=peak - before if peak is not None and before is not None else None,
                wer=scored.wer,
                term_recall=scored.term_recall,
            )
        )
    return results
