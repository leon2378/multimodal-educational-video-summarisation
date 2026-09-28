"""Where the pipeline keeps its cache, and which speech model it runs."""

from pathlib import Path

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
