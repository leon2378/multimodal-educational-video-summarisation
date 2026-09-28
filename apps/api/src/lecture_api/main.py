"""App factory. Run with `uvicorn lecture_api.main:create_app --factory`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lecture_api.routes import health, lectures, processing, results
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
        app.state.temporal = None
        app.state.temporal_lock = asyncio.Lock()
        yield
        await engine.dispose()

    app = FastAPI(title="Lecture Summariser API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    # The web app calls the API straight from the browser, including the progress stream.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(lectures.router, prefix="/v1")
    app.include_router(processing.router, prefix="/v1")
    app.include_router(results.router, prefix="/v1")
    return app
