"""Which model runs the pipeline's LLM steps, and where the prompts live."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "provider:model". Google's free tier suits development on public lectures; Google may use
    # what's sent through it to improve its products, so switch to a paid tier for anything else.
    llm_model: str = "google:gemini-3.5-flash-lite"
    gemini_api_key: SecretStr | None = None
    prompts_dir: Path = Path("prompts/pipeline")
    # Slide images per vision request. Fewer requests matter on free-tier daily limits.
    slides_per_request: int = 8
