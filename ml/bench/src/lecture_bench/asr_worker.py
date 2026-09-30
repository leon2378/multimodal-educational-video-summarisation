"""Speech recognition timings, run inside the GPU worker image, where CUDA and faster-whisper are.

    python asr_worker.py AUDIO OUT_JSON cuda:float16 cuda:int8_float16 cuda:int8 cpu:int8

Each variant is `device:compute_type`, transcribed with the pipeline's own transcriber and
settings. A second of silence first loads the model, so the timing is recognition alone. GPU
memory is sampled for the whole card while it runs. Imports only what the image has.
"""

import io
import json
import sys
import threading
import time
import wave
from pathlib import Path

from lecture_perception.asr import FasterWhisperTranscriber, WhisperConfig

MODEL_PATH = "data/models/faster-whisper-large-v3-turbo"
MODEL_ID = "faster-whisper-large-v3-turbo@0a363e9"


def gpu_used_mb() -> float | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetMemoryInfo(handle).used) / 2**20
    except Exception:
        return None


def silence(seconds: float = 1.0, rate: int = 16_000) -> io.BytesIO:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(2 * int(seconds * rate)))  # 16-bit zeros
    buffer.seek(0)
    return buffer


def run(audio: Path, variant: str) -> dict[str, object]:
    device, compute_type = variant.split(":")
    before = gpu_used_mb()
    transcriber = FasterWhisperTranscriber(
        WhisperConfig(MODEL_PATH, MODEL_ID, device=device, compute_type=compute_type)
    )
    started = time.monotonic()
    transcriber.transcribe(silence())
    load_s = time.monotonic() - started

    peak = [gpu_used_mb()]
    done = threading.Event()

    def sample() -> None:
        while not done.is_set():
            used = gpu_used_mb()
            if used is not None and (peak[0] is None or used > peak[0]):
                peak[0] = used
            time.sleep(0.1)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    with audio.open("rb") as f:
        transcript = transcriber.transcribe(f)
    seconds = time.monotonic() - started
    done.set()
    sampler.join()
    transcriber.close()
    return {
        "variant": variant,
        "load_s": load_s,
        "seconds": seconds,
        "audio_s": transcript.duration_s,
        "gpu_mb_before": before,
        "gpu_mb_peak": peak[0],
        "text": " ".join(segment.text for segment in transcript.segments),
    }


def main() -> None:
    audio, out, *variants = sys.argv[1:]
    results = []
    for variant in variants:
        results.append(run(Path(audio), variant))
        Path(out).write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"{variant}: {results[-1]['seconds']:.1f} s", flush=True)


if __name__ == "__main__":
    main()
