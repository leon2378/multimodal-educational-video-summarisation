import os
from collections.abc import Iterator
from pathlib import Path

import docker
import pytest
from pydantic_settings import BaseSettings

from tests.unit import synthetic


@pytest.fixture(autouse=True, scope="session")
def _without_dotenv() -> Iterator[None]:
    """Tests see what CI sees: no developer's .env. Its OTEL_ENDPOINT and Langfuse keys would
    switch telemetry on (an app started by one test traces SQL for every test after it, and
    exporters retry a collector that isn't running), and its API keys would reach real services."""
    with pytest.MonkeyPatch.context() as patch:
        for settings in _settings_classes(BaseSettings):
            patch.setitem(settings.model_config, "env_file", None)
        yield


def _settings_classes(base: type[BaseSettings]) -> Iterator[type[BaseSettings]]:
    """The project's settings classes, all defined by the time the tests are collected."""
    for cls in base.__subclasses__():
        if cls.__module__.startswith("lecture_"):
            yield cls
        yield from _settings_classes(cls)


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests locally when Docker isn't running. In CI they must run, not skip."""
    if os.environ.get("CI") or _docker_available():
        return
    skip = pytest.mark.skip(reason="Docker is not running; integration tests need it")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 12-second lecture: slides, camera shots, a build and a revisit (see synthetic.py)."""
    return synthetic.write_video(tmp_path_factory.mktemp("media") / "lecture.mp4")
