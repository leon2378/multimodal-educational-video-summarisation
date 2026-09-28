from pathlib import Path

import pytest

from tests.unit import synthetic


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return synthetic.write_video(tmp_path_factory.mktemp("media") / "lecture.mp4")
