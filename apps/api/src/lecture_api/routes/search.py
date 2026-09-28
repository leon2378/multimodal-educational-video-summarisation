"""Search across processed lectures: hybrid retrieval with reranking (packages/rag)."""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from starlette.concurrency import run_in_threadpool

from lecture_api.deps import SearcherDep, SettingsDep
from lecture_api.schemas import SearchHitOut, SearchResults
from lecture_rag.search import SearchMode

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    searcher: SearcherDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=1, max_length=500, pattern=r"\S")],
    lecture_id: Annotated[
        list[uuid.UUID] | None, Query(description="Search only these lectures (repeatable).")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
    mode: Annotated[
        SearchMode | None,
        Query(description="Default: the server's `SEARCH_MODE`. `rerank` ranks best."),
    ] = None,
) -> SearchResults:
    """The passages that best match `q`, best first, each with its place in the video."""
    mode = mode or SearchMode(settings.search_mode)
    try:
        # The clients are synchronous (the workers share them), so run them off the event loop.
        hits = await run_in_threadpool(
            searcher.search, q, lecture_ids=lecture_id, limit=limit, mode=mode
        )
    except (httpx.HTTPError, ResponseHandlingException, UnexpectedResponse) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable right now."
        ) from error
    return SearchResults(
        query=q, mode=mode, hits=[SearchHitOut.model_validate(hit.model_dump()) for hit in hits]
    )
