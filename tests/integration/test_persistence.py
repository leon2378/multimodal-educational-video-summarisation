from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import BaseModel

from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_pipeline.cache import StageCache, StageSpec

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "packages" / "core" / "alembic.ini"


def test_migrations_match_models(settings: Settings) -> None:
    """Fails when a model changed without a migration. Fix with `make revision m="..."`."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.check(config)


class Chapters(BaseModel):
    titles: list[str]


def test_stage_cache_round_trips_through_object_storage(settings: Settings) -> None:
    cache = StageCache(ObjectStorage(settings))
    spec = StageSpec(name="chapters", version="1", model="gemini-flash-lite", params={"max": 12})
    inputs = {"timeline": "sha256:9a7e"}

    first = cache.run(spec, inputs, Chapters, lambda: Chapters(titles=["Intro", "Memoisation"]))
    second = cache.get(spec, inputs, Chapters)

    assert not first.cached
    assert second is not None
    assert second.cached
    assert second.output == first.output
