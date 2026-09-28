# 0004: Hosted LLMs through Pydantic AI, Gemini's free tier for development

- Status: Accepted
- Date: 2026-09-28
- Code: `packages/llm`

## Context

The pipeline needs a vision model that reads slides, and a text model that plans chapters and
writes notes. The development machine has a 6 GB GPU that speech recognition already uses, which
leaves no room for a useful vision model at the same time. The options:

- **Hosted APIs** (Gemini, Claude): nothing to run, strong vision, pay per token.
- **Self-hosted on Modal** (Qwen3-VL-8B or Qwen3.5-9B on vLLM): full control, pay per GPU-second,
  more to operate.
- **Local small models** (Qwen3.5-4B quantised in Ollama): free and offline, but it only fits on
  the 6 GB card once ASR has unloaded, and it's much weaker at reading dense slides.

## Decision

Use hosted models through Pydantic AI, with the model chosen by one setting (`LLM_MODEL`, as
`provider:model`). Development defaults to `google:gemini-3.5-flash-lite` on Gemini's free tier,
used only on public, openly licensed lectures. Paid runs switch the setting to a paid Gemini tier or
Claude Haiku 4.5 (which needs its provider added in `lecture_llm.models`). Self-hosting waits
until cost or privacy calls for it, and will get its own ADR.

## Consequences

- Swapping models is a config change. Prompts are versioned files, and the model and prompt
  fingerprint go into each stage's cache key, so a swap re-runs only the LLM stages.
- Free-tier limits shape the design. Limits are per project and per day (20 requests a day on
  `gemini-3.8-flash`, hit during the baseline work), and Google sheds free-tier load with 503
  "high demand" errors. So slides are read 8 to a request, and the SDK retries 429 and 5xx with
  backoff. A 51-minute lecture takes about 13 requests.
- Google may use free-tier inputs to improve its products. That's acceptable for MIT OCW material
  and not for anything private: those runs need a paid tier.
- Each LLM stage records requests and tokens. Converting them to dollars per run comes with cost
  tracking in Phase 4.
- Local models stay possible behind the same interface for offline development
  (the `offline-llm` Compose profile in the blueprint).
