from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from lecture_api.main import create_app
from lecture_core.settings import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Nothing here touches Postgres or storage: engines and boto3 clients connect lazily.
    settings = Settings(database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")
    with TestClient(create_app(settings)) as client:
        yield client


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


def test_openapi_lists_phase_1_routes(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/healthz",
        "/readyz",
        "/v1/lectures",
        "/v1/lectures/{lecture_id}",
        "/v1/lectures/{lecture_id}/complete-upload",
    } <= paths
