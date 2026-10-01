"""Where the pipeline keeps its cache, which speech model it runs, and who reads slides."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Local run: stage cache and artifacts on disk. Workers (Phase 2b) use object storage.
    pipeline_store: Path = Path("data/pipeline")
    pipeline_runs_dir: Path = Path("data/pipeline-runs")

    # Pinned download: mobiuslabsgmbh/faster-whisper-large-v3-turbo @ 0a363e9 (MIT).
    whisper_model_path: str = "data/models/faster-whisper-large-v3-turbo"
    whisper_model_id: str = "faster-whisper-large-v3-turbo@0a363e9"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_language: str | None = None
    # Where speech recognition runs: "local" loads the model in the worker (on the device above);
    # "modal" calls a GPU in Modal (infra/modal/asr.py, ADR 0010), for a host without one.
    # Modal's client reads MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.
    transcriber: Literal["local", "modal"] = "local"
    modal_asr_app: str = "lecture-asr"

    # Who reads the slides (lecture_pipeline.stages.read_slides): "routed" sends only slides with
    # figures, annotations or doubtful OCR to the vision LLM; "vlm" sends every slide; "ocr" none.
    slide_reader: Literal["vlm", "routed", "ocr"] = "routed"
    # Lectures from a link (lecture_perception.fetch, docs/adr/0011-lectures-from-any-link.md):
    # the longest video to download, and how long a download may take.
    fetch_max_duration_s: int = 3 * 3600
    fetch_timeout_s: int = 3600
    # Optional, for YouTube, which often refuses cloud servers. Both are secrets, and both are
    # used for YouTube links only. A Netscape cookies.txt from a signed-in account, base64
    # encoded to fit on one line of .env (`base64 -w0 cookies.txt`; a spare account: Google may
    # flag one used this way), and a proxy for YouTube's traffic, http://user:password@host:port.
    youtube_cookies_b64: SecretStr | None = None
    youtube_proxy: SecretStr | None = None
