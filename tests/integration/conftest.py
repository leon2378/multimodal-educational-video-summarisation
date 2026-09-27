"""Real Postgres and SeaweedFS in throwaway containers, shared across the test session."""

import time
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from lecture_api.main import create_app
from lecture_core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
# Keep these in step with infra/compose.yaml.
POSTGRES_IMAGE = "postgres:17-alpine"
SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.47"
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
