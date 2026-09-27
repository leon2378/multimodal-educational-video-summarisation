"""Gemini API prices, so every baseline run records what it cost.

Source: https://ai.google.dev/gemini-api/docs/pricing (paid tier, checked 2026-09-27).
Prices change often. For a model missing here, pass --input-price and --output-price.
"""

from datetime import date

from pydantic import BaseModel


class Price(BaseModel):
    """US dollars per million tokens. Output prices include thinking tokens."""

    input: float
    output: float
    # Set when audio input costs more than text, image and video input.
    input_audio: float | None = None
    # Prompts longer than this many tokens are billed at the long_* prices.
    long_context_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None


class TokenUsage(BaseModel):
    prompt_total: int
    prompt_by_modality: dict[str, int]
    # Tokens from tools the model ran itself (e.g. agentic video navigation), billed as input.
    tool_use_prompt: int
    output: int
    thinking: int


_FLASH_2026 = Price(input=0.75, output=3.75)
_FLASH_2027 = Price(input=1.50, output=7.50)
_ALWAYS = date.min

# Model -> [(effective from, price)], oldest first.
PRICES: dict[str, list[tuple[date, Price]]] = {
    "gemini-3.8-flash": [(_ALWAYS, _FLASH_2026), (date(2027, 1, 1), _FLASH_2027)],
    "gemini-3.7-flash": [(_ALWAYS, _FLASH_2026), (date(2027, 1, 1), _FLASH_2027)],
    "gemini-3.6-flash": [(_ALWAYS, _FLASH_2026), (date(2027, 1, 1), _FLASH_2027)],
    "gemini-3.5-flash-lite": [(_ALWAYS, Price(input=0.30, output=2.50))],
    "gemini-3.1-flash-lite": [(_ALWAYS, Price(input=0.25, input_audio=0.50, output=1.50))],
    "gemini-3.1-pro-preview": [
        (
            _ALWAYS,
            Price(
                input=2.00,
                output=12.00,
                long_context_threshold=200_000,
                long_input=4.00,
                long_output=18.00,
            ),
        )
    ],
    "gemini-2.5-flash": [(_ALWAYS, Price(input=0.30, input_audio=1.00, output=2.50))],
}


def price_for(model: str, on: date) -> Price | None:
    entries = PRICES.get(model)
    if entries is None:
        return None
    return [price for since, price in entries if since <= on][-1]


def cost_usd(usage: TokenUsage, price: Price) -> float:
    long_context = (
        price.long_context_threshold is not None
        and usage.prompt_total > price.long_context_threshold
    )
    input_rate = price.long_input if long_context and price.long_input else price.input
    output_rate = price.long_output if long_context and price.long_output else price.output

    audio = usage.prompt_by_modality.get("AUDIO", 0) if price.input_audio is not None else 0
    other_input = usage.prompt_total - audio + usage.tool_use_prompt
    dollars = (
        other_input * input_rate
        + audio * (price.input_audio or 0)
        + (usage.output + usage.thinking) * output_rate
    )
    return dollars / 1_000_000
