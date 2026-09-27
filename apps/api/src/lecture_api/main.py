"""App factory. Run with `uvicorn lecture_api.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lecture_api.routes import health, lectures
from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.storage = ObjectStorage(settings)
        yield
        await engine.dispose()

    app = FastAPI(title="Lecture Summariser API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(lectures.router, prefix="/v1")
    return app
