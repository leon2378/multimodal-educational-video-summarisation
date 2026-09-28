import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from lecture_api.deps import get_searcher
from lecture_api.main import create_app
from lecture_core.settings import Settings
from lecture_core.timeline import Timeline, TimelineSegment
from lecture_rag.chunks import build_chunks
from lecture_rag.encoders import embed_chunks
from lecture_rag.index import SearchIndex
from lecture_rag.search import Searcher
from tests.unit.fakes import FakeDense, FakeReranker, FakeSparse

UNREACHABLE = "http://127.0.0.1:1"


@pytest.fixture
def app() -> FastAPI:
    # Nothing here touches Postgres, storage, Temporal or the search services: they all
    # connect lazily.
    settings = Settings(
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        temporal_address="127.0.0.1:1",
        qdrant_url=UNREACHABLE,
        embeddings_url=UNREACHABLE,
        reranker_url=UNREACHABLE,
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def searcher() -> Iterator[Searcher]:
    lecture_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    timeline = Timeline(
        duration_s=60.0,
        segments=[
            TimelineSegment(
                id="s1", start_s=0, end_s=30, transcript="hash tables", slide_id=None, words=[]
            ),
            TimelineSegment(
                id="s2", start_s=30, end_s=60, transcript="binary search", slide_id=None, words=[]
            ),
        ],
        slides=[],
    )
    index = SearchIndex(QdrantClient(":memory:"), "segments")
    embeddings = embed_chunks(build_chunks(timeline), FakeDense(), FakeSparse())
    index.replace_lecture(lecture_id, embeddings.chunks, [])
    yield Searcher(index, FakeDense(), FakeSparse(), FakeReranker())
    index.close()


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_lecture_rejects_non_video(client: TestClient) -> None:
    response = client.post(
        "/v1/lectures",
        json={"title": "Slides", "filename": "slides.pdf", "content_type": "application/pdf"},
    )
    assert response.status_code == 422


def test_the_web_app_origin_may_call_the_api(client: TestClient) -> None:
    allowed = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    other = client.get("/healthz", headers={"Origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in other.headers


def test_processing_is_unavailable_while_temporal_is_down(client: TestClient) -> None:
    response = client.post(f"/v1/lectures/{uuid.uuid4()}/process")
    assert response.status_code == 503
    assert response.json()["detail"] == "Processing is unavailable right now."


def test_openapi_lists_the_routes(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/healthz",
        "/readyz",
        "/v1/lectures",
        "/v1/lectures/{lecture_id}",
        "/v1/lectures/{lecture_id}/complete-upload",
        "/v1/lectures/{lecture_id}/media",
        "/v1/lectures/{lecture_id}/process",
        "/v1/lectures/{lecture_id}/runs",
        "/v1/lectures/{lecture_id}/events",
        "/v1/lectures/{lecture_id}/transcript",
        "/v1/lectures/{lecture_id}/slides",
        "/v1/lectures/{lecture_id}/timeline",
        "/v1/lectures/{lecture_id}/notes",
        "/v1/search",
    } <= paths


def test_search(app: FastAPI, client: TestClient, searcher: Searcher) -> None:
    app.dependency_overrides[get_searcher] = lambda: searcher

    response = client.get("/v1/search", params={"q": "binary search", "limit": 1})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["query"], body["mode"]) == ("binary search", "hybrid")
    [hit] = body["hits"]
    assert (hit["segment_id"], hit["start_s"], hit["end_s"]) == ("s2", 30, 60)
    assert (hit["transcript"], hit["text"], hit["slide_title"]) == ("binary search",) * 2 + (None,)


def test_search_mode_can_be_chosen(app: FastAPI, client: TestClient, searcher: Searcher) -> None:
    app.dependency_overrides[get_searcher] = lambda: searcher

    response = client.get("/v1/search", params={"q": "binary search", "mode": "bm25"})

    assert response.json()["mode"] == "bm25"
    assert response.json()["hits"][0]["segment_id"] == "s2"


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {"q": "   "}, {"q": "x" * 501}, {"q": "x", "limit": 21}, {"q": "x", "mode": "?"}],
)
def test_search_rejects_bad_parameters(client: TestClient, params: dict[str, str | int]) -> None:
    assert client.get("/v1/search", params=params).status_code == 422


def test_search_is_unavailable_while_its_services_are_down(client: TestClient) -> None:
    response = client.get("/v1/search", params={"q": "binary search"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Search is unavailable right now."
