import uuid
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from lecture_api.main import create_app
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage

pytestmark = pytest.mark.integration

FAKE_VIDEO = b"\x00\x00\x00\x18ftypmp42 not really a video"


def create_lecture(client: TestClient, title: str = "Dynamic programming") -> dict[str, Any]:
    response = client.post(
        "/v1/lectures",
        json={
            "title": title,
            "filename": "6.006-lecture-15.mp4",
            "content_type": "video/mp4",
            "licence": "CC BY-NC-SA 4.0",
            "attribution": "MIT OpenCourseWare",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def upload(created: dict[str, Any], data: bytes = FAKE_VIDEO) -> None:
    target = created["upload"]
    response = httpx2.request(
        target["method"], target["url"], content=data, headers=target["headers"]
    )
    assert response.status_code == 200, response.text


def test_readyz(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "storage": "ok"}


def test_upload_flow(client: TestClient) -> None:
    created = create_lecture(client)
    lecture_id = created["lecture"]["id"]
    assert created["lecture"]["status"] == "awaiting_upload"

    upload(created)
    completed = client.post(f"/v1/lectures/{lecture_id}/complete-upload")

    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "uploaded"
    assert completed.json()["size_bytes"] == len(FAKE_VIDEO)
    assert client.get(f"/v1/lectures/{lecture_id}").json()["status"] == "uploaded"
    assert lecture_id in {lecture["id"] for lecture in client.get("/v1/lectures").json()}

    # The player streams the video straight from storage through a presigned URL.
    media = client.get(f"/v1/lectures/{lecture_id}/media").json()
    assert media["content_type"] == "video/mp4"
    assert httpx2.get(media["url"]).content == FAKE_VIDEO


def test_media_needs_an_upload(client: TestClient) -> None:
    lecture_id = create_lecture(client)["lecture"]["id"]
    assert client.get(f"/v1/lectures/{lecture_id}/media").status_code == 409


def test_complete_upload_is_idempotent(client: TestClient) -> None:
    created = create_lecture(client)
    lecture_id = created["lecture"]["id"]
    upload(created)

    first = client.post(f"/v1/lectures/{lecture_id}/complete-upload")
    second = client.post(f"/v1/lectures/{lecture_id}/complete-upload")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_complete_upload_before_uploading_is_a_conflict(client: TestClient) -> None:
    lecture_id = create_lecture(client)["lecture"]["id"]
    response = client.post(f"/v1/lectures/{lecture_id}/complete-upload")
    assert response.status_code == 409


def test_oversized_upload_is_rejected_and_deleted(settings: Settings) -> None:
    small_limit = settings.model_copy(update={"max_upload_bytes": 4})
    with TestClient(create_app(small_limit)) as client:
        created = create_lecture(client)
        upload(created)

        response = client.post(f"/v1/lectures/{created['lecture']['id']}/complete-upload")

    assert response.status_code == 413
    key = f"raw/{created['lecture']['id']}/source.mp4"
    assert ObjectStorage(settings).head(key) is None


def test_unknown_lecture_is_404(client: TestClient) -> None:
    assert client.get(f"/v1/lectures/{uuid.uuid4()}").status_code == 404
