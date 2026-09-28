"""Phase 2b end to end: upload through the API, process with Temporal workers, read results.

The workers run in this process with the fake speech model and LLM (tests/unit/fakes.py), so
this exercises the workflow, activities, stage cache in object storage, persistence and the
search index.
"""

import json
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def upload_lecture(client: TestClient, data: bytes, title: str) -> str:
    created = client.post(
        "/v1/lectures",
        json={"title": title, "filename": "lecture.mp4", "content_type": "video/mp4"},
    ).json()
    target = created["upload"]
    put = httpx2.request(target["method"], target["url"], content=data, headers=target["headers"])
    assert put.status_code == 200, put.text
    lecture_id: str = created["lecture"]["id"]
    assert client.post(f"/v1/lectures/{lecture_id}/complete-upload").status_code == 200
    return lecture_id


def wait_for(client: TestClient, lecture_id: str, timeout_s: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        lecture: dict[str, Any] = client.get(f"/v1/lectures/{lecture_id}").json()
        if lecture["status"] in {"ready", "failed"}:
            return lecture
        time.sleep(0.5)
    raise AssertionError(f"lecture {lecture_id} still processing after {timeout_s}s")


def test_upload_process_and_read_results(
    processing_client: TestClient, synthetic_video: Path
) -> None:
    client = processing_client
    lecture_id = upload_lecture(client, synthetic_video.read_bytes(), "Synthetic lecture")

    started = client.post(f"/v1/lectures/{lecture_id}/process")
    again = client.post(f"/v1/lectures/{lecture_id}/process")

    assert started.status_code == 202, started.text
    assert again.json()["id"] == started.json()["id"]  # idempotent while running
    lecture = wait_for(client, lecture_id)
    assert lecture["status"] == "ready"
    assert len(lecture["content_hash"]) == 64

    notes = client.get(f"/v1/lectures/{lecture_id}/notes").json()
    assert [c["title"] for c in notes["notes"]["chapters"]] == ["Memoisation", "Growth"]
    assert [c["term"] for c in notes["notes"]["concepts"]] == ["memoisation"]

    transcript = client.get(f"/v1/lectures/{lecture_id}/transcript").json()
    assert transcript[0]["text"] == "welcome back everyone"
    timeline = client.get(f"/v1/lectures/{lecture_id}/timeline").json()
    assert [(s["segment_id"], s["slide_id"]) for s in timeline] == [
        ("s000", None),
        ("s001", 0),
        ("s002", 1),
        ("s003", 0),
    ]

    slides = client.get(f"/v1/lectures/{lecture_id}/slides").json()
    assert [s["slide_id"] for s in slides] == [0, 1]
    assert [len(s["spans"]) for s in slides] == [2, 1]  # slide 0 is shown twice
    image = httpx2.get(slides[0]["image_url"])
    assert image.status_code == 200
    assert image.content[:3] == b"\xff\xd8\xff"  # a JPEG, served straight from storage

    # Indexed for search before it was marked ready.
    search = client.get("/v1/search", params={"q": "big O notation", "lecture_id": lecture_id})
    hits = search.json()["hits"]
    assert (hits[0]["segment_id"], hits[0]["chapter"]) == ("s002", "Growth")
    assert {hit["lecture_id"] for hit in hits} == {lecture_id}

    (run,) = client.get(f"/v1/lectures/{lecture_id}/runs").json()
    assert run["status"] == "succeeded"
    stages = {s["stage"] for s in run["stages"]}
    assert stages >= {"probe", "asr", "slides", "notes", "embed", "index"}
    assert run["llm_usage"]["requests"] > 0


def test_progress_stream_ends_with_the_run(
    processing_client: TestClient, synthetic_video: Path
) -> None:
    client = processing_client
    lecture_id = upload_lecture(client, synthetic_video.read_bytes(), "Streamed")
    client.post(f"/v1/lectures/{lecture_id}/process")

    events = []
    with client.stream("GET", f"/v1/lectures/{lecture_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))

    assert events[-1]["progress"]["status"] == "succeeded"
    assert events[-1]["lecture_status"] == "ready"


def test_a_file_that_isnt_video_fails_fast(processing_client: TestClient) -> None:
    client = processing_client
    lecture_id = upload_lecture(client, b"definitely not a video", "Broken")

    client.post(f"/v1/lectures/{lecture_id}/process")

    assert wait_for(client, lecture_id, timeout_s=60)["status"] == "failed"
    (run,) = client.get(f"/v1/lectures/{lecture_id}/runs").json()
    assert run["status"] == "failed"
    assert "isn't a readable video" in run["error"]
    # Retrying can't fix bad input, so the stage isn't retried: the run fails in seconds.
    assert client.get(f"/v1/lectures/{lecture_id}/notes").status_code == 404


def test_processing_needs_an_upload(processing_client: TestClient) -> None:
    created = processing_client.post(
        "/v1/lectures",
        json={"title": "Not uploaded", "filename": "x.mp4", "content_type": "video/mp4"},
    ).json()
    response = processing_client.post(f"/v1/lectures/{created['lecture']['id']}/process")
    assert response.status_code == 409
