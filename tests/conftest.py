import os
from pathlib import Path

import docker
import pytest

from tests.unit import synthetic


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
