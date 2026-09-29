"""Observability: telemetry setup, the Langfuse span filter, and the app's metrics."""

import base64
import logging
from typing import Any

import pytest
import sqlalchemy
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from lecture_api.schemas import MessageOut
from lecture_core import metrics, telemetry
from lecture_core.processing import StageInfo
from lecture_core.settings import Settings
from lecture_llm.agents import Usage
from lecture_llm.telemetry import record_usage
from lecture_pipeline.temporal import activities


class Recorder:
    """Stands in for an OpenTelemetry instrument."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, Any]]] = []

    def add(self, value: float, attributes: dict[str, Any]) -> None:
        self.calls.append((value, attributes))

    record = add


def test_telemetry_is_off_until_configured() -> None:
    observed = telemetry.setup("test", Settings(otel_endpoint=None, langfuse_public_key=None))
    assert not observed.enabled
    observed.shutdown()


def test_only_llm_spans_reach_the_filtered_exporter() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        telemetry.ScopeFilter(telemetry.LLM_SCOPE, SimpleSpanProcessor(exporter))
    )

    llm, database = provider.get_tracer("pydantic-ai"), provider.get_tracer("sqlalchemy")
    with llm.start_as_current_span("chat gemini"), database.start_as_current_span("SELECT"):
        pass

    assert [span.name for span in exporter.get_finished_spans()] == ["chat gemini"]


def test_langfuse_gets_the_llm_spans_with_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    exporters: list[dict[str, Any]] = []

    class Exporter(InMemorySpanExporter):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            exporters.append(kwargs)

    installed: dict[str, Any] = {}
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", Exporter)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda p: installed.update(t=p))
    settings = Settings(
        otel_endpoint=None,
        langfuse_public_key="pk-lf-1",
        langfuse_secret_key=SecretStr("sk-lf-2"),
        langfuse_host="https://langfuse.example/",
    )

    observed = telemetry.setup("test", settings)

    assert observed.enabled
    token = base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    assert exporters == [
        {
            "endpoint": "https://langfuse.example/api/public/otel/v1/traces",
            "headers": {"Authorization": f"Basic {token}"},
        }
    ]
    processors = installed["t"]._active_span_processor._span_processors
    assert [type(p) for p in processors] == [telemetry.ScopeFilter]
    observed.shutdown()


def test_an_otlp_endpoint_also_exports_metrics_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: dict[str, Any] = {}
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda p: installed.update(t=p))
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", lambda p: installed.update(m=p))
    monkeypatch.setattr(telemetry, "set_logger_provider", lambda p: installed.update(log=p))
    root = logging.getLogger()
    before = list(root.handlers)

    observed = telemetry.setup("test", Settings(otel_endpoint="http://collector:4318/"))
    try:
        assert set(installed) == {"t", "m", "log"}
        assert any(isinstance(h, LoggingHandler) for h in root.handlers)
    finally:
        observed.shutdown()
        root.handlers = before


def test_sql_statements_become_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    engine = sqlalchemy.create_engine("sqlite://")

    telemetry.trace_queries(engine)
    try:
        with engine.connect() as connection:
            connection.execute(sqlalchemy.text("SELECT 1"))
    finally:
        SQLAlchemyInstrumentor().uninstrument()

    assert any(span.name.startswith("SELECT") for span in exporter.get_finished_spans())


def test_llm_usage_is_counted_with_its_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens, cost = Recorder(), Recorder()
    monkeypatch.setattr(metrics, "llm_tokens", tokens)
    monkeypatch.setattr(metrics, "llm_cost", cost)

    spent = record_usage(
        Usage(requests=2, input_tokens=1_000_000, output_tokens=100_000),
        "google:gemini-3.5-flash-lite",
        "qa",
    )

    assert spent == pytest.approx(0.55)
    labels = {"model": "google:gemini-3.5-flash-lite", "purpose": "qa"}
    assert tokens.calls == [
        (1_000_000, {**labels, "direction": "input"}),
        (100_000, {**labels, "direction": "output"}),
    ]
    [(dollars, cost_labels)] = cost.calls
    assert dollars == pytest.approx(0.55)
    assert cost_labels == labels
    # A model without a price still counts its tokens.
    assert record_usage(Usage(input_tokens=5), "function:fake", "qa") is None
    assert len(cost.calls) == 1


def test_stages_are_timed_by_whether_the_cache_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    durations, runs = Recorder(), Recorder()
    monkeypatch.setattr(metrics, "stage_duration", durations)
    monkeypatch.setattr(metrics, "stage_runs", runs)

    activities._measured(StageInfo(stage="asr", seconds=65.0, cached=False))

    assert durations.calls == [(65.0, {"stage": "asr", "cached": False})]
    assert runs.calls == [(1, {"stage": "asr", "cached": False})]


def test_answers_report_their_cost() -> None:
    answer = MessageOut.model_validate(
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "role": "assistant",
            "content": "a",
            "created_at": "2026-09-29T00:00:00Z",
            "model": "google:gemini-3.5-flash-lite",
            "usage": {"requests": 1, "input_tokens": 4000, "output_tokens": 200},
        }
    )

    assert answer.cost_usd == pytest.approx((4000 * 0.30 + 200 * 2.50) / 1_000_000)
    assert "cost_usd" in answer.model_dump()
