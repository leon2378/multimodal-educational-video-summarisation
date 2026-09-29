"""LLM calls in traces and metrics.

Pydantic AI traces every agent run and model call itself (model, tokens, prompts and replies)
once instrumented; images are left out, so slide JPEGs don't end up in traces. The token and
cost counters (lecture_core.metrics) are recorded here, by model and purpose, only for calls
that actually ran: a cached stage costs nothing.
"""

from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from lecture_core import metrics
from lecture_llm.agents import Usage
from lecture_llm.pricing import text_cost_usd


def instrument_agents() -> None:
    """Trace all agents through the global tracer and meter providers."""
    Agent.instrument_all(InstrumentationSettings(include_binary_content=False))


def record_usage(usage: Usage, model: str, purpose: str) -> float | None:
    """Count the tokens and what they cost. Returns the cost, or None for a model without a
    price (lecture_llm.pricing)."""
    attributes = {"model": model, "purpose": purpose}
    metrics.llm_tokens.add(usage.input_tokens, {**attributes, "direction": "input"})
    metrics.llm_tokens.add(usage.output_tokens, {**attributes, "direction": "output"})
    cost = text_cost_usd(model, usage.input_tokens, usage.output_tokens)
    if cost is not None:
        metrics.llm_cost.add(cost, attributes)
    return cost
