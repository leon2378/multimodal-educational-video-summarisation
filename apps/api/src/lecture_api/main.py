"""App factory. Run with `uvicorn lecture_api.main:create_app --factory`."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lecture_api.routes import courses, health, lectures, processing, qa, results, search
from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_llm.models import LLMConfigError, make_model
from lecture_llm.qa import AnswerLLM, QAPrompts
from lecture_llm.settings import LLMSettings
from lecture_rag.services import SearchServices

logger = logging.getLogger(__name__)


def create_answerer() -> AnswerLLM | None:
    """The Q&A model, or None without one configured: the rest of the API still works."""
    llm_settings = LLMSettings()
    try:
        model = make_model(llm_settings)
    except LLMConfigError as error:
        logger.warning("Q&A is off: %s", error)
        return None
    return AnswerLLM(model, QAPrompts.load(llm_settings.qa_prompts_dir))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.storage = ObjectStorage(settings)
        app.state.temporal = None
        app.state.temporal_lock = asyncio.Lock()
        search_services = SearchServices.from_settings(settings)
        app.state.searcher = search_services.searcher()
        app.state.answerer = create_answerer()
        yield
        search_services.close()
        await engine.dispose()

    app = FastAPI(title="Lecture Summariser API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    # The web app calls the API straight from the browser, including the progress stream.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(courses.router, prefix="/v1")
    app.include_router(lectures.router, prefix="/v1")
    app.include_router(processing.router, prefix="/v1")
    app.include_router(results.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(qa.router, prefix="/v1")
    return app
