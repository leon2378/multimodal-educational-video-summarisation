"""Temporal worker: `lecture-worker --queues cpu,llm` (CPU worker) or `--queues gpu`.

Each queue a process serves gets its own Worker. The GPU queue runs one activity at a time: a
6 GB card holds one speech model, not two.
"""

import argparse
import asyncio
import contextlib
import logging
import sys
import tempfile
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from opentelemetry import metrics
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from pydantic_settings import BaseSettings, SettingsConfigDict
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.runtime import OpenTelemetryConfig, Runtime, TelemetryConfig
from temporalio.worker import Worker

from lecture_core import telemetry
from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.processing import QUEUE_CPU, QUEUE_GPU, QUEUE_LLM
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_llm.agents import LectureLLM, Prompts
from lecture_llm.models import make_model
from lecture_llm.settings import LLMSettings
from lecture_llm.telemetry import instrument_agents
from lecture_perception.asr import FasterWhisperTranscriber, WhisperConfig
from lecture_pipeline.settings import PipelineSettings
from lecture_pipeline.temporal.activities import PipelineActivities, Resources, SearchResources
from lecture_pipeline.temporal.workflow import ProcessLecture
from lecture_rag.services import SearchServices

QUEUES = (QUEUE_CPU, QUEUE_GPU, QUEUE_LLM)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Source videos are downloaded here once per worker, named by their SHA-256.
    worker_media_dir: Path = Path(tempfile.gettempdir()) / "lecture-media"
    worker_cpu_concurrency: int = 4
    worker_llm_concurrency: int = 4


def build_resources(queues: Sequence[str], traced: bool = False) -> Resources:
    """Only what these queues need: the GPU worker loads no LLM, the CPU worker no speech model."""
    settings = Settings()
    worker = WorkerSettings()
    resources = Resources(storage=ObjectStorage(settings), media_dir=worker.worker_media_dir)
    if QUEUE_CPU in queues:
        engine = create_engine(settings)
        if traced:
            telemetry.trace_queries(engine.sync_engine)
        resources.sessionmaker = create_sessionmaker(engine)
        search = SearchServices.from_settings(settings)
        resources.search = SearchResources(search.index, search.dense, search.sparse)
    if QUEUE_LLM in queues:
        llm_settings = LLMSettings()
        resources.llm = LectureLLM(
            make_model(llm_settings),
            Prompts.load(llm_settings.prompts_dir),
            llm_settings.slides_per_request,
        )
    if QUEUE_GPU in queues:
        pipeline = PipelineSettings()
        resources.transcriber = FasterWhisperTranscriber(
            WhisperConfig(
                model_path=pipeline.whisper_model_path,
                model_id=pipeline.whisper_model_id,
                device=pipeline.whisper_device,
                compute_type=pipeline.whisper_compute_type,
                language=pipeline.whisper_language,
            )
        )
    return resources


def build_workers(
    client: Client, activities: PipelineActivities, queues: Sequence[str]
) -> list[Worker]:
    settings = WorkerSettings()
    workers = []
    if QUEUE_CPU in queues:
        workers.append(
            Worker(
                client,
                task_queue=QUEUE_CPU,
                workflows=[ProcessLecture],
                activities=[
                    activities.ingest,
                    activities.detect_slides,
                    activities.build_timeline,
                    activities.assemble_notes,
                    activities.embed_segments,
                    activities.index_lecture,
                    activities.persist_results,
                    activities.mark_failed,
                ],
                activity_executor=ThreadPoolExecutor(settings.worker_cpu_concurrency),
                max_concurrent_activities=settings.worker_cpu_concurrency,
            )
        )
    if QUEUE_LLM in queues:
        workers.append(
            Worker(
                client,
                task_queue=QUEUE_LLM,
                activities=[
                    activities.read_slides,
                    activities.plan_chapters,
                    activities.draft_notes,
                ],
                activity_executor=ThreadPoolExecutor(settings.worker_llm_concurrency),
                max_concurrent_activities=settings.worker_llm_concurrency,
            )
        )
    if QUEUE_GPU in queues:
        workers.append(
            Worker(
                client,
                task_queue=QUEUE_GPU,
                activities=[activities.transcribe],
                activity_executor=ThreadPoolExecutor(1),
                max_concurrent_activities=1,
            )
        )
    return workers


async def connect(settings: Settings, traced_as: str | None = None) -> Client:
    """A Temporal client. Traced as a service, it carries trace context into workflows and
    activities, and Temporal's own metrics (task queue latency, slots in use) go to the same
    collector."""
    runtime = None
    if traced_as and settings.otel_endpoint:
        otlp = OpenTelemetryConfig(
            url=f"{settings.otel_endpoint.rstrip('/')}/v1/metrics", http=True
        )
        # Every worker's metrics come from "temporal-core-sdk"; the tag tells them apart.
        telemetry_config = TelemetryConfig(metrics=otlp, global_tags={"worker": traced_as})
        runtime = Runtime(telemetry=telemetry_config)
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor()] if traced_as else [],
        runtime=runtime,
    )


async def run(queues: Sequence[str]) -> None:
    settings = Settings()
    service = "lecture-gpu-worker" if QUEUE_GPU in queues else "lecture-cpu-worker"
    observed = telemetry.setup(service, settings)
    if observed.enabled:
        instrument_agents()
        HTTPXClientInstrumentor().instrument()
        if QUEUE_GPU in queues:
            _watch_gpu_memory()
    client = await connect(settings, traced_as=service if observed.enabled else None)
    resources = build_resources(queues, traced=observed.enabled)
    workers = build_workers(client, PipelineActivities(resources), queues)
    logging.info("serving task queues: %s", ", ".join(queues))
    try:
        await asyncio.gather(*(worker.run() for worker in workers))
    finally:
        observed.shutdown()


def _watch_gpu_memory() -> None:
    """GPU memory in use, as a gauge. NVIDIA's NVML comes with the driver; without it (no GPU,
    or not the GPU image) there's simply no gauge."""
    try:
        import pynvml

        pynvml.nvmlInit()
        device = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        logging.warning("no NVML: GPU memory won't be reported")
        return

    def observe(_options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pynvml.nvmlDeviceGetMemoryInfo(device).used)

    metrics.get_meter("lecture-summariser").create_observable_gauge(
        "lecture.gpu.memory.used", callbacks=[observe], unit="By", description="GPU memory in use"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lecture-worker", description=__doc__)
    parser.add_argument(
        "--queues",
        default=f"{QUEUE_CPU},{QUEUE_LLM}",
        help=f"comma-separated task queues to serve, from: {', '.join(QUEUES)}",
    )
    args = parser.parse_args(argv)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    if unknown := [q for q in queues if q not in QUEUES]:
        parser.error(f"unknown queue(s): {', '.join(unknown)}")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(queues))
    return 0


if __name__ == "__main__":
    sys.exit(main())
