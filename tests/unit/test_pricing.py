from datetime import date

import pytest

from lecture_evals.pricing import Price, TokenUsage, cost_usd, price_for


def usage(prompt: int, output: int = 0, thinking: int = 0, audio: int = 0) -> TokenUsage:
    return TokenUsage(
        prompt_total=prompt,
        prompt_by_modality={"AUDIO": audio, "VIDEO": prompt - audio},
        tool_use_prompt=0,
        output=output,
        thinking=thinking,
    )


def test_flash_price_doubles_in_2027() -> None:
    assert price_for("gemini-3.8-flash", date(2026, 12, 31)) == Price(input=0.75, output=3.75)
    assert price_for("gemini-3.8-flash", date(2027, 1, 1)) == Price(input=1.50, output=7.50)


def test_unknown_model_has_no_price() -> None:
    assert price_for("gemini-99-ultra", date(2026, 9, 27)) is None


def test_thinking_tokens_bill_as_output() -> None:
    price = Price(input=1.0, output=10.0)
    assert cost_usd(usage(1_000_000, output=100_000, thinking=100_000), price) == pytest.approx(3.0)


def test_audio_billed_separately_when_priced_separately() -> None:
    price = Price(input=0.30, input_audio=1.00, output=2.50)
    # 600k video tokens at $0.30/M + 400k audio tokens at $1.00/M
    assert cost_usd(usage(1_000_000, audio=400_000), price) == pytest.approx(0.18 + 0.40)


def test_long_prompts_use_long_context_prices() -> None:
    price = price_for("gemini-3.1-pro-preview", date(2026, 9, 27))
    assert price is not None
    assert cost_usd(usage(200_000), price) == pytest.approx(0.40)  # at the threshold: $2/M
    assert cost_usd(usage(250_000), price) == pytest.approx(1.00)  # above it: $4/M
