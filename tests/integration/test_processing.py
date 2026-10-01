"""Phase 2b end to end: upload through the API (or give a link), process with Temporal workers,
read results, then delete the lecture.

The workers run in this process with the fake speech model and LLM (tests/unit/fakes.py), so
this exercises the workflow, activities, stage cache in object storage, persistence and the
search index.
"""

import functools
import http.server
import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage, source_key
from tests.integration.helpers import ask, upload_lecture, wait_for

pytestmark = pytest.mark.integration


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


def test_deleting_a_lecture_removes_it_everywhere(
    processing_client: TestClient, processed_lecture: str, processing_settings: Settings
) -> None:
    client = processing_client
    lecture_id = processed_lecture
    filename = client.get(f"/v1/lectures/{lecture_id}").json()["source_filename"]
    thread_id = ask(client, f"/v1/lectures/{lecture_id}/ask", "What is memoisation?")[0][
        "thread_id"
    ]
    storage = ObjectStorage(processing_settings)
    video = source_key(uuid.UUID(lecture_id), filename)
    qdrant = QdrantClient(url=processing_settings.qdrant_url)
    its_points = models.Filter(
        must=[models.FieldCondition(key="lecture_id", match=models.MatchValue(value=lecture_id))]
    )

    def points() -> int:
        return qdrant.count(processing_settings.qdrant_collection, count_filter=its_points).count

    assert storage.head(video) is not None
    assert points() > 0

    assert client.delete(f"/v1/lectures/{lecture_id}").status_code == 204

    assert client.get(f"/v1/lectures/{lecture_id}").status_code == 404
    assert client.get(f"/v1/threads/{thread_id}").status_code == 404
    assert storage.head(video) is None
    assert points() == 0
    assert client.delete(f"/v1/lectures/{lecture_id}").status_code == 404
    qdrant.close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def site(synthetic_video: Path) -> Iterator[str]:
    """A web server on this machine with the synthetic lecture on it."""
    handler = functools.partial(_QuietHandler, directory=str(synthetic_video.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_a_lecture_from_a_link_is_downloaded_then_processed(
    processing_client: TestClient, site: str, synthetic_video: Path
) -> None:
    client = processing_client
    url = f"{site}/{synthetic_video.name}"

    created = client.post("/v1/lectures/from-url", json={"url": url})

    assert created.status_code == 201, created.text
    lecture = created.json()
    assert (lecture["status"], lecture["source_url"]) == ("processing", url)
    assert lecture["title"] == "Video from 127.0.0.1"
    done = wait_for(client, lecture["id"])
    assert done["status"] == "ready", done
    # Named by the download: a direct link's title is its file's name.
    assert (done["title"], done["source_filename"]) == ("lecture", "lecture.mp4")
    assert done["size_bytes"] == synthetic_video.stat().st_size
    (run,) = client.get(f"/v1/lectures/{lecture['id']}/runs").json()
    assert (run["stages"][0]["stage"], run["stages"][0]["cached"]) == ("fetch", False)
    # Processed again, it isn't downloaded again.
    client.post(f"/v1/lectures/{lecture['id']}/process")
    assert wait_for(client, lecture["id"])["status"] == "ready"
    latest = client.get(f"/v1/lectures/{lecture['id']}/runs").json()[0]
    assert (latest["stages"][0]["stage"], latest["stages"][0]["cached"]) == ("fetch", True)


def test_a_link_that_cant_be_downloaded_fails_with_the_reason(
    processing_client: TestClient, site: str
) -> None:
    client = processing_client
    created = client.post(
        "/v1/lectures/from-url", json={"url": f"{site}/missing.mp4", "title": "Missing"}
    ).json()

    done = wait_for(client, created["id"], timeout_s=60)

    assert (done["status"], done["title"]) == ("failed", "Missing")
    (run,) = client.get(f"/v1/lectures/{created['id']}/runs").json()
    assert "Nothing was found at that link." in run["error"]
