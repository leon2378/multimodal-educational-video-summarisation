import os

import docker
import pytest


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
