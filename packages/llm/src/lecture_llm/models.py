"""Build the Pydantic AI model named in settings."""

from google.genai.types import HttpRetryOptions
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from lecture_llm.settings import LLMSettings


class LLMConfigError(ValueError):
    pass


def make_model(settings: LLMSettings) -> Model:
    provider, _, name = settings.llm_model.partition(":")
    if provider == "google":
        if settings.gemini_api_key is None:
            raise LLMConfigError("Set GEMINI_API_KEY in .env or the environment.")
        return GoogleModel(
            name,
            provider=GoogleProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                # 429 and 5xx ("high demand") are common on the free tier. These calls aren't
                # streamed, so the SDK can retry them whole.
                retry_options=HttpRetryOptions(
                    attempts=5,
                    initial_delay=5,
                    max_delay=60,
                    http_status_codes=[429, 500, 502, 503, 504],
                ),
            ),
        )
    raise LLMConfigError(f"unsupported model provider {provider!r} in {settings.llm_model!r}")
