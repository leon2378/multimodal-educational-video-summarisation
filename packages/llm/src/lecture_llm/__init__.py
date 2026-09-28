"""LLM agents (slide reading, chapters, notes) behind Pydantic AI, with usage tracking."""

import pydantic_ai

# Pydantic AI 2.x prints a banner on the first run when no observability is configured.
# It's noise in worker logs and CLI output.
pydantic_ai.BANNER_ENABLED = False
