"""OpenTelemetry for the API and workers: traces, metrics and logs (docs/architecture.md).

Off unless OTEL_ENDPOINT is set. In the Compose stack that's the `otel-lgtm` container (the
`observability` profile), whose Grafana shows traces in Tempo, metrics in Prometheus and logs in
Loki. With both Langfuse keys set, the LLM calls (Pydantic AI's spans, prompts and replies
included) also go to Langfuse; nothing else does.
"""

import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import Engine

from lecture_core.settings import Settings

# The tracer Pydantic AI's instrumentation uses (agent runs and model calls).
LLM_SCOPE = "pydantic-ai"


class ScopeFilter(SpanProcessor):
    """Passes on only the spans from one instrumentation scope, to a processor that should see
    nothing else (Langfuse wants the LLM calls, not every database query)."""

    def __init__(self, scope: str, processor: SpanProcessor) -> None:
        self._scope = scope
        self._processor = processor

    def _wanted(self, span: ReadableSpan) -> bool:
        scope = span.instrumentation_scope
        return scope is not None and scope.name == self._scope

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        if self._wanted(span):
            self._processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if self._wanted(span):
            self._processor.on_end(span)

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis)


@dataclass
class Telemetry:
    enabled: bool
    shutdown: Callable[[], None]


def setup(service: str, settings: Settings) -> Telemetry:
    """Install global tracer, meter and logger providers for this process, if configured."""
    langfuse = settings.langfuse_public_key and settings.langfuse_secret_key
    if not settings.otel_endpoint and not langfuse:
        return Telemetry(enabled=False, shutdown=lambda: None)
    resource = Resource.create({"service.name": service, "service.namespace": "lecture-summariser"})
    tracer_provider = TracerProvider(resource=resource)
    shutdowns: list[Callable[[], object]] = [tracer_provider.shutdown]

    if settings.otel_endpoint:
        endpoint = settings.otel_endpoint.rstrip("/")
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                    export_interval_millis=15_000,
                )
            ],
        )
        metrics.set_meter_provider(meter_provider)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
        )
        set_logger_provider(logger_provider)
        logging.getLogger().addHandler(
            LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        )
        shutdowns += [meter_provider.shutdown, logger_provider.shutdown]

    if langfuse and settings.langfuse_secret_key is not None:
        pair = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key.get_secret_value()}"
        auth = base64.b64encode(pair.encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        tracer_provider.add_span_processor(ScopeFilter(LLM_SCOPE, BatchSpanProcessor(exporter)))

    trace.set_tracer_provider(tracer_provider)

    def shutdown() -> None:
        for close in reversed(shutdowns):
            close()

    return Telemetry(enabled=True, shutdown=shutdown)


def trace_queries(engine: Engine) -> None:
    """A span for each SQL statement the engine runs (for an async engine, pass its sync_engine)."""
    # The instrumentation declares SQLAlchemy < 2.1 and, without skip_dep_check, quietly does
    # nothing on 2.1; the engine events it listens to haven't changed.
    SQLAlchemyInstrumentor().instrument(engine=engine, skip_dep_check=True)
