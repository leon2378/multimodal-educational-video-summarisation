"""Request dependencies, backed by resources created in the app lifespan."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from lecture_core.models import Course, Lecture
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage
from lecture_llm.qa import AnswerLLM
from lecture_rag.search import Searcher


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_searcher(request: Request) -> Searcher:
    searcher: Searcher = request.app.state.searcher
    return searcher


def get_answerer(request: Request) -> AnswerLLM:
    answerer: AnswerLLM | None = request.app.state.answerer
    if answerer is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Answers are unavailable: the server has no language model configured.",
        )
    return answerer


async def get_temporal(request: Request) -> Client:
    """Connects on first use rather than at startup, so the API still serves uploads and
    results while Temporal is down."""
    state = request.app.state
    async with state.temporal_lock:
        if state.temporal is None:
            settings: Settings = state.settings
            try:
                state.temporal = await Client.connect(
                    settings.temporal_address,
                    namespace=settings.temporal_namespace,
                    data_converter=pydantic_data_converter,
                    # Links a process request's trace to the workflow and its activities.
                    interceptors=[TracingInterceptor()] if state.traced else [],
                )
            except RuntimeError as error:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Processing is unavailable right now."
                ) from error
    client: Client = state.temporal
    return client


async def lecture_or_404(session: AsyncSession, lecture_id: uuid.UUID) -> Lecture:
    lecture = await session.get(Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lecture not found.")
    return lecture


async def course_or_404(session: AsyncSession, course_id: uuid.UUID) -> Course:
    course = await session.get(Course, course_id)
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")
    return course


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
TemporalDep = Annotated[Client, Depends(get_temporal)]
SearcherDep = Annotated[Searcher, Depends(get_searcher)]
AnswererDep = Annotated[AnswerLLM, Depends(get_answerer)]
