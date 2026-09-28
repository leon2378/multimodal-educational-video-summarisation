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
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.processing import QUEUE_CPU, QUEUE_GPU, QUEUE_LLM
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_llm.agents import LectureLLM, Prompts
from lecture_llm.models import make_model
from lecture_llm.settings import LLMSettings
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


def build_resources(queues: Sequence[str]) -> Resources:
    """Only what these queues need: the GPU worker loads no LLM, the CPU worker no speech model."""
    settings = Settings()
    worker = WorkerSettings()
    resources = Resources(storage=ObjectStorage(settings), media_dir=worker.worker_media_dir)
    if QUEUE_CPU in queues:
        resources.sessionmaker = create_sessionmaker(create_engine(settings))
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


async def connect(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


async def run(queues: Sequence[str]) -> None:
    client = await connect(Settings())
    workers = build_workers(client, PipelineActivities(build_resources(queues)), queues)
    logging.info("serving task queues: %s", ", ".join(queues))
    await asyncio.gather(*(worker.run() for worker in workers))


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
