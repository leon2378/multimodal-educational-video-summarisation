"""Speech recognition on a GPU in Modal, for workers on hosts without one (ADR 0010).

A worker with TRANSCRIBER=modal (lecture_perception.asr.ModalTranscriber) sends `transcribe` the
audio and the settings it wants, and gets progress back as the transcription goes, then the
transcript. The code and the model are a local GPU worker's (FasterWhisperTranscriber, int8
weights with float16 compute), so the stage cache treats their transcripts alike.

    make modal-model    # once: the pinned model into a Modal Volume
    make modal          # deploy this app and infra/modal/embeddings.py
"""

import io
import queue
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import modal

# As in lecture_pipeline.settings, and the README's download for the local GPU worker.
MODEL_ID = "faster-whisper-large-v3-turbo@0a363e9"
MODEL_URL = (
    "https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/"
    "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
)
MODEL_FILES = ["config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]
MODELS = Path("/models")
MODEL_DIR = MODELS / "faster-whisper-large-v3-turbo"
SITE_PACKAGES = "/usr/local/lib/python3.12/site-packages"


def _locked(*names: str) -> list[str]:
    """Requirements pinned to uv.lock, so Modal runs what the GPU worker image runs."""
    lock = tomllib.loads((Path(__file__).parents[2] / "uv.lock").read_text(encoding="utf-8"))
    versions = {package["name"]: package["version"] for package in lock["package"]}
    return [f"{name}=={versions[name]}" for name in names]


image = modal.Image.debian_slim(python_version="3.12")
# The image is built from the repo when deploying; in Modal's container this module is imported
# again, without uv.lock, and the image it describes there isn't used.
if modal.is_local():
    image = image.uv_pip_install(
        *_locked(
            "faster-whisper",
            "ctranslate2",
            "onnxruntime",
            "tokenizers",
            "av",
            "nvidia-cublas-cu12",
            "nvidia-cudnn-cu12",
            "pydantic",
        )
    )
image = image.env(
    # CTranslate2 finds cuBLAS and cuDNN here, as in infra/docker/worker.Dockerfile.
    {"LD_LIBRARY_PATH": f"{SITE_PACKAGES}/nvidia/cublas/lib:{SITE_PACKAGES}/nvidia/cudnn/lib"}
).add_local_python_source("lecture_core", "lecture_perception")

app = modal.App("lecture-asr")
models = modal.Volume.from_name("lecture-asr-models", create_if_missing=True)


@app.function(image=image, volumes={MODELS: models}, timeout=30 * 60)
def download_model() -> None:
    """The pinned model into the Volume (about 1.6 GB), once before the first transcription."""
    import urllib.request

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in [*MODEL_FILES, "model.bin"]:
        urllib.request.urlretrieve(f"{MODEL_URL}/{name}", MODEL_DIR / name)  # noqa: S310 - fixed https URL
    models.commit()


# An L4 transcribes a lecture-hour in a minute or two; the container stays up two minutes after
# the last call, so a second upload soon after skips the cold start.
@app.function(
    image=image, gpu="L4", volumes={MODELS: models}, timeout=2 * 60 * 60, scaledown_window=120
)
def transcribe(audio: bytes, model_id: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yields {"progress": 0.0 to 1.0} while it works, then {"transcript": ...}. `params` are
    the worker's WhisperConfig.cache_params(), so the settings that make the cache key are the
    ones used."""
    from lecture_perception.asr import FasterWhisperTranscriber, WhisperConfig

    if model_id != MODEL_ID:
        raise ValueError(f"This app has {MODEL_ID}, not {model_id}: update and redeploy it.")
    if not (MODEL_DIR / "model.bin").exists():
        raise RuntimeError("The model isn't in the Volume yet: run `make modal-model`.")
    transcriber = FasterWhisperTranscriber(
        WhisperConfig(str(MODEL_DIR), model_id, device="cuda", **params)
    )
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def run() -> None:
        try:
            transcript = transcriber.transcribe(
                io.BytesIO(audio), lambda done: events.put(("progress", done))
            )
            events.put(("transcript", transcript.model_dump(mode="json")))
        except BaseException as error:  # handed to the caller below
            events.put(("error", error))

    threading.Thread(target=run, daemon=True).start()
    # Before the model loads, so the worker's heartbeats start as soon as the GPU is up.
    yield {"progress": 0.0}
    while True:
        kind, value = events.get()
        if kind == "error":
            raise value
        yield {kind: value}
        if kind == "transcript":
            return
