"""Search across processed lectures: hybrid retrieval with reranking (packages/rag)."""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from lecture_api.access import course_or_404, lecture_or_404, readable
from lecture_api.auth import ViewerDep
from lecture_api.deps import SearcherDep, SessionDep, SettingsDep
from lecture_api.schemas import SearchHitOut, SearchResults
from lecture_core.models import Lecture
from lecture_rag.search import SearchMode

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    searcher: SearcherDep,
    settings: SettingsDep,
    session: SessionDep,
    viewer: ViewerDep,
    q: Annotated[str, Query(min_length=1, max_length=500, pattern=r"\S")],
    lecture_id: Annotated[
        list[uuid.UUID] | None, Query(description="Search only these lectures (repeatable).")
    ] = None,
    course_id: Annotated[
        uuid.UUID | None, Query(description="Search only the lectures in this course.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
    mode: Annotated[
        SearchMode | None,
        Query(description="Default: the server's `SEARCH_MODE`. `rerank` ranks best."),
    ] = None,
) -> SearchResults:
    """The passages that best match `q`, best first, each with its place in the video. Only
    lectures the caller may read are searched: the public ones, and their own."""
    mode = mode or SearchMode(settings.search_mode)
    lecture_ids = lecture_id
    if course_id is not None:
        if lecture_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Search a course or lectures, not both."
            )
        await course_or_404(session, course_id, viewer)
        lecture_ids = list(
            await session.scalars(
                select(Lecture.id).where(Lecture.course_id == course_id, readable(Lecture, viewer))
            )
        )
    elif lecture_ids:
        for wanted in lecture_ids:
            await lecture_or_404(session, wanted, viewer)
    elif not viewer.admin:
        lecture_ids = list(
            await session.scalars(select(Lecture.id).where(readable(Lecture, viewer)))
        )
    if lecture_ids is not None and not lecture_ids:
        return SearchResults(query=q, mode=mode, hits=[])
    try:
        # The clients are synchronous (the workers share them), so run them off the event loop.
        hits = await run_in_threadpool(
            searcher.search, q, lecture_ids=lecture_ids, limit=limit, mode=mode
        )
    except (httpx.HTTPError, ResponseHandlingException, UnexpectedResponse) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable right now."
        ) from error
    return SearchResults(
        query=q, mode=mode, hits=[SearchHitOut.model_validate(hit.model_dump()) for hit in hits]
    )
