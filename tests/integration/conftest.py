"""Real Postgres, SeaweedFS, Temporal and Qdrant in throwaway containers, shared across the
session. The models (speech, LLM, embeddings, reranker) are fakes."""

import asyncio
import contextlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import boto3
import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from temporalio.client import Client
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from lecture_api.deps import get_answerer, get_searcher
from lecture_api.main import create_app
from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.processing import QUEUE_CPU, QUEUE_GPU, QUEUE_LLM
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_llm.agents import LectureLLM, Prompts
from lecture_llm.qa import AnswerLLM, QAPrompts
from lecture_pipeline.temporal.activities import PipelineActivities, Resources, SearchResources
from lecture_pipeline.temporal.worker import build_workers, connect
from lecture_rag.index import SearchIndex
from lecture_rag.search import Searcher
from tests.integration.helpers import upload_lecture, wait_for
from tests.unit.fakes import (
    FakeDense,
    FakeLLM,
    FakeQA,
    FakeReranker,
    FakeSparse,
    FakeTranscriber,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# Keep these in step with infra/compose.yaml.
POSTGRES_IMAGE = "postgres:17-alpine"
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.47"
TEMPORAL_IMAGE = "temporalio/temporal:1.9.1"
QDRANT_IMAGE = "qdrant/qdrant:v1.19.1"
BUCKET = "lectures"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[str]:
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_command(f"mini -bucket={BUCKET} -s3.config=/etc/seaweedfs/s3.json")
        .with_volume_mapping(
            str(REPO_ROOT / "infra" / "seaweedfs" / "s3.json"), "/etc/seaweedfs/s3.json", "ro"
        )
        .with_exposed_ports(8333)
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8333)}"
        _wait_for_bucket(endpoint)
        yield endpoint


@pytest.fixture(scope="session")
def settings(database_url: str, s3_endpoint: str) -> Settings:
    settings = Settings(database_url=database_url, s3_endpoint_url=s3_endpoint)
    alembic_config = Config(str(REPO_ROOT / "packages" / "core" / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    return settings


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture(scope="session")
def temporal_address() -> Iterator[str]:
    container = (
        DockerContainer(TEMPORAL_IMAGE)
        .with_command("server start-dev --ip 0.0.0.0 --headless")
        .with_exposed_ports(7233)
    )
    with container:
        address = f"{container.get_container_host_ip()}:{container.get_exposed_port(7233)}"
        _wait_for_temporal(address)
        yield address


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    container = DockerContainer(QDRANT_IMAGE).with_exposed_ports(6333)
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"
        _wait_for_http(f"{url}/readyz")
        yield url


@pytest.fixture(scope="session")
def processing_settings(settings: Settings, temporal_address: str, qdrant_url: str) -> Settings:
    return settings.model_copy(
        update={"temporal_address": temporal_address, "qdrant_url": qdrant_url}
    )


@pytest.fixture(scope="session")
def workers(
    processing_settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """All three task queues served in this process, with fake speech and LLM models."""
    with InProcessWorkers(processing_settings, tmp_path_factory.mktemp("worker-media")):
        yield


@pytest.fixture
def fake_qa() -> FakeQA:
    return FakeQA()


@pytest.fixture
def processing_client(
    processing_settings: Settings, workers: None, fake_qa: FakeQA
) -> Iterator[TestClient]:
    app = create_app(processing_settings)
    index = _search_index(processing_settings)
    app.dependency_overrides[get_searcher] = lambda: Searcher(
        index, FakeDense(), FakeSparse(), FakeReranker()
    )
    answerer = AnswerLLM(fake_qa.model, QAPrompts.load(REPO_ROOT / "prompts" / "qa"))
    app.dependency_overrides[get_answerer] = lambda: answerer
    with TestClient(app) as client:
        yield client
    index.close()


@pytest.fixture
def processed_lecture(processing_client: TestClient, synthetic_video: Path) -> str:
    """The synthetic lecture, uploaded and processed; its id."""
    lecture_id = upload_lecture(processing_client, synthetic_video.read_bytes(), "Processed")
    processing_client.post(f"/v1/lectures/{lecture_id}/process")
    assert wait_for(processing_client, lecture_id)["status"] == "ready"
    return lecture_id


def _search_index(settings: Settings) -> SearchIndex:
    return SearchIndex(QdrantClient(url=settings.qdrant_url), settings.qdrant_collection)


class InProcessWorkers:
    """Temporal workers on their own thread and event loop: the async engine and the Temporal
    client must be created on the loop that uses them."""

    def __init__(self, settings: Settings, media_dir: Path) -> None:
        self.settings, self.media_dir = settings, media_dir
        self._ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None

    def __enter__(self) -> "InProcessWorkers":
        self._thread.start()
        if not self._ready.wait(60) or self._error is not None:
            raise RuntimeError("workers didn't start") from self._error
        return self

    def __exit__(self, *_: object) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(30)

    async def _main(self) -> None:
        try:
            self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
            client = await connect(self.settings)
            engine = create_engine(self.settings)
            resources = Resources(
                storage=ObjectStorage(self.settings),
                media_dir=self.media_dir,
                sessionmaker=create_sessionmaker(engine),
                llm=LectureLLM(FakeLLM().model, Prompts.load(REPO_ROOT / "prompts" / "pipeline")),
                transcriber=FakeTranscriber(),
                search=SearchResources(_search_index(self.settings), FakeDense(), FakeSparse()),
            )
            queues = [QUEUE_CPU, QUEUE_GPU, QUEUE_LLM]
            async with contextlib.AsyncExitStack() as stack:
                for worker in build_workers(client, PipelineActivities(resources), queues):
                    await stack.enter_async_context(worker)
                self._ready.set()
                await self._stop.wait()
            await engine.dispose()
        except BaseException as error:
            self._error = error
            self._ready.set()
            raise


def _wait_for_temporal(address: str, timeout_s: float = 60) -> None:
    async def attempt() -> None:
        client = await Client.connect(address)
        await client.count_workflows()

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            asyncio.run(attempt())
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _wait_for_http(url: str, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            httpx.get(url).raise_for_status()
            return
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _wait_for_bucket(endpoint: str, timeout_s: float = 60) -> None:
    defaults = Settings()
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=defaults.s3_region,
        aws_access_key_id=defaults.s3_access_key_id,
        aws_secret_access_key=defaults.s3_secret_access_key.get_secret_value(),
    )
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            s3.head_bucket(Bucket=BUCKET)
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
