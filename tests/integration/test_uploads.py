"""Uploads in parts on real SeaweedFS: resumed after an interruption, refused when they don't
fit, and dropped with their lecture (docs/adr/0012-resumable-uploads-in-parts.md)."""

import os
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import boto3
import httpx2
import pytest
from fastapi.testclient import TestClient

from lecture_api.deps import get_searcher
from lecture_api.main import create_app
from lecture_core.settings import Settings
from lecture_core.storage import MIN_PART_BYTES, ObjectStorage

pytestmark = pytest.mark.integration

# Three parts: two whole ones and a short last one.
VIDEO = os.urandom(2 * MIN_PART_BYTES + 12345)


@pytest.fixture
def small_parts(settings: Settings) -> Settings:
    return settings.model_copy(update={"upload_part_bytes": MIN_PART_BYTES})


@pytest.fixture
def client(small_parts: Settings) -> Iterator[TestClient]:
    app = create_app(small_parts)
    # Deleting a lecture drops its passages from the search index, which isn't running here.
    index = SimpleNamespace(delete_lecture=lambda _lecture_id: None)
    app.dependency_overrides[get_searcher] = lambda: SimpleNamespace(index=index)
    with TestClient(app) as client:
        yield client


def create(client: TestClient, size: int = len(VIDEO), title: str = "In parts") -> str:
    response = client.post(
        "/v1/lectures",
        json={
            "title": title,
            "filename": "talk.mp4",
            "content_type": "video/mp4",
            "size_bytes": size,
        },
    )
    assert response.status_code == 201, response.text
    lecture_id: str = response.json()["lecture"]["id"]
    return lecture_id


def parts_of(client: TestClient, lecture_id: str, size: int = len(VIDEO)) -> dict[str, Any]:
    response = client.post(f"/v1/lectures/{lecture_id}/upload-parts", json={"size_bytes": size})
    assert response.status_code == 200, response.text
    plan: dict[str, Any] = response.json()
    return plan


def send(plan: dict[str, Any], part: dict[str, Any], data: bytes = VIDEO) -> int:
    start = (part["number"] - 1) * plan["part_bytes"]
    return httpx2.put(part["url"], content=data[start : start + plan["part_bytes"]]).status_code


def complete(client: TestClient, lecture_id: str) -> httpx2.Response:
    return client.post(f"/v1/lectures/{lecture_id}/complete-upload")


def stored(client: TestClient, lecture_id: str) -> bytes:
    media = client.get(f"/v1/lectures/{lecture_id}/media").json()
    return httpx2.get(media["url"]).content


def open_uploads(settings: Settings, lecture_id: str) -> list[str]:
    """The ids of the lecture's unfinished uploads in parts, straight from storage."""
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    listed = s3.list_multipart_uploads(Bucket=settings.s3_bucket, Prefix=f"raw/{lecture_id}/")
    return [upload["UploadId"] for upload in listed.get("Uploads", [])]


def test_a_video_uploads_in_parts(client: TestClient) -> None:
    lecture_id = create(client)
    lecture = client.get(f"/v1/lectures/{lecture_id}").json()
    assert (lecture["status"], lecture["size_bytes"]) == ("awaiting_upload", len(VIDEO))

    plan = parts_of(client, lecture_id)
    assert (plan["count"], plan["part_bytes"], plan["uploaded"]) == (3, MIN_PART_BYTES, [])
    for part in reversed(plan["parts"]):  # any order does
        assert send(plan, part) == 200
    completed = complete(client, lecture_id)

    assert completed.status_code == 200, completed.text
    assert (completed.json()["status"], completed.json()["size_bytes"]) == ("uploaded", len(VIDEO))
    assert stored(client, lecture_id) == VIDEO
    # Finished: confirming again changes nothing, and there are no parts left to send.
    assert complete(client, lecture_id).json() == completed.json()
    again = client.post(f"/v1/lectures/{lecture_id}/upload-parts", json={"size_bytes": len(VIDEO)})
    assert again.status_code == 409


def test_an_interrupted_upload_resumes_where_it_stopped(client: TestClient) -> None:
    lecture_id = create(client)
    plan = parts_of(client, lecture_id)
    first, _, last = plan["parts"]
    assert send(plan, first) == send(plan, last) == 200

    # The connection dropped before part 2 went.
    early = complete(client, lecture_id)
    assert early.status_code == 409
    assert early.json()["detail"] == "Part 2 of 3 hasn't been uploaded yet."

    resumed = parts_of(client, lecture_id)
    assert resumed["uploaded"] == [1, 3]
    assert [part["number"] for part in resumed["parts"]] == [2]
    assert send(resumed, resumed["parts"][0]) == 200
    assert complete(client, lecture_id).status_code == 200
    assert stored(client, lecture_id) == VIDEO


def test_storage_refuses_a_part_of_the_wrong_size(client: TestClient) -> None:
    lecture_id = create(client)
    plan = parts_of(client, lecture_id)
    url = plan["parts"][0]["url"]

    assert httpx2.put(url, content=VIDEO[: MIN_PART_BYTES + 1]).status_code == 403
    assert httpx2.put(url, content=VIDEO[: MIN_PART_BYTES - 1]).status_code == 403
    assert parts_of(client, lecture_id)["uploaded"] == []


def test_resuming_takes_the_same_file(client: TestClient) -> None:
    lecture_id = create(client)
    parts_of(client, lecture_id)

    other = client.post(
        f"/v1/lectures/{lecture_id}/upload-parts", json={"size_bytes": len(VIDEO) + 1}
    )

    assert other.status_code == 409
    assert "Choose the same file" in other.json()["detail"]


def test_a_file_over_the_limit_is_refused_before_it_uploads(small_parts: Settings) -> None:
    limited = small_parts.model_copy(update={"max_upload_bytes": MIN_PART_BYTES})
    with TestClient(create_app(limited)) as client:
        title = f"Too big {uuid.uuid4()}"
        response = client.post(
            "/v1/lectures",
            json={
                "title": title,
                "filename": "talk.mp4",
                "content_type": "video/mp4",
                "size_bytes": MIN_PART_BYTES + 1,
            },
        )
        assert response.status_code == 413
        assert title not in {lecture["title"] for lecture in client.get("/v1/lectures").json()}

        lecture_id = create(client, size=MIN_PART_BYTES)
        bigger = client.post(
            f"/v1/lectures/{lecture_id}/upload-parts", json={"size_bytes": MIN_PART_BYTES + 1}
        )
        assert bigger.status_code == 413


def test_deleting_a_lecture_mid_upload_drops_its_parts(
    client: TestClient, settings: Settings
) -> None:
    lecture_id = create(client)
    plan = parts_of(client, lecture_id)
    assert send(plan, plan["parts"][0]) == 200
    assert len(open_uploads(settings, lecture_id)) == 1

    assert client.delete(f"/v1/lectures/{lecture_id}").status_code == 204

    assert open_uploads(settings, lecture_id) == []


def test_an_upload_cleared_unfinished_starts_again(client: TestClient, settings: Settings) -> None:
    lecture_id = create(client)
    plan = parts_of(client, lecture_id)
    assert send(plan, plan["parts"][0]) == 200
    # What the bucket's lifecycle rule does to an upload left unfinished for a week.
    (upload_id,) = open_uploads(settings, lecture_id)
    ObjectStorage(settings).abort_upload(f"raw/{lecture_id}/source.mp4", upload_id)

    assert complete(client, lecture_id).status_code == 409
    restarted = parts_of(client, lecture_id)
    assert restarted["uploaded"] == []
    for part in restarted["parts"]:
        assert send(restarted, part) == 200
    assert complete(client, lecture_id).status_code == 200
    assert stored(client, lecture_id) == VIDEO
