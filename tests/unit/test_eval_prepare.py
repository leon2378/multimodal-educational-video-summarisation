"""lecture-eval --prepare: media fetched and checked, the lecture processed through the API; and
the gate leaving out search modes that weren't run."""

import hashlib
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from lecture_evals import prepare
from lecture_evals.cli import _unmeasured
from lecture_evals.golden import GoldenLecture
from lecture_evals.runs import Bound

VIDEO = b"not really a video"
LECTURE = GoldenLecture(
    title="T",
    video="v.mp4",
    video_sha256=hashlib.sha256(VIDEO).hexdigest(),
    duration="00:10",
    source="https://example.org",
    licence="CC BY-NC-SA 4.0",
    attribution="Someone",
)
LECTURE_ID = uuid.UUID("00000000-0000-4000-8000-00000000000c")


def test_missing_media_is_downloaded_once_and_always_checked(tmp_path: Path) -> None:
    downloads = []

    def respond(request: httpx.Request) -> httpx.Response:
        downloads.append(str(request.url))
        return httpx.Response(200, content=VIDEO)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    video = prepare.MediaFile(tmp_path / "v.mp4", LECTURE.video_sha256, "https://media/v.mp4")

    prepare.fetch([video], client)
    prepare.fetch([video], client)

    assert video.path.read_bytes() == VIDEO
    assert downloads == ["https://media/v.mp4"]
    with pytest.raises(LookupError, match="SHA-256"):
        prepare.fetch([prepare.MediaFile(video.path, "0" * 64, None)], client)
    with pytest.raises(LookupError, match="no URL"):
        prepare.fetch([prepare.MediaFile(tmp_path / "gone.srt", "0" * 64, None)], client)
    # A download that doesn't match isn't kept.
    wrong = prepare.MediaFile(tmp_path / "w.mp4", "0" * 64, "https://media/w.mp4")
    with pytest.raises(LookupError, match="SHA-256"):
        prepare.fetch([wrong], client)
    assert not wrong.path.exists()


def _api(
    statuses: list[str], lectures: list[dict[str, Any]], error: str | None = None
) -> tuple[httpx.Client, list[str]]:
    """The API and storage, as far as preparing uses them, and the calls they saw."""
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/v1/lectures" and request.method == "GET":
            return httpx.Response(200, json=lectures)
        if path == "/v1/lectures":
            upload = {
                "url": "http://storage/lectures/v.mp4",
                "headers": {"Content-Type": "video/mp4"},
            }
            return httpx.Response(201, json={"lecture": {"id": str(LECTURE_ID)}, "upload": upload})
        if path == "/lectures/v.mp4":
            assert request.content == VIDEO
            return httpx.Response(200)
        if path == f"/v1/lectures/{LECTURE_ID}":
            return httpx.Response(200, json={"status": statuses.pop(0)})
        if path == f"/v1/lectures/{LECTURE_ID}/runs":
            stages = [{"stage": "asr", "seconds": 41.0, "cached": False}]
            return httpx.Response(200, json=[{"error": error, "stages": stages}])
        return httpx.Response(202)  # complete-upload, process

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond)), calls


def test_a_new_lecture_is_uploaded_processed_and_waited_for(tmp_path: Path) -> None:
    (tmp_path / "v.mp4").write_bytes(VIDEO)
    client, calls = _api(["processing", "processing", "ready"], lectures=[])

    assert prepare.process(client, LECTURE, tmp_path / "v.mp4", poll_s=0) == LECTURE_ID
    assert calls == [
        "GET /v1/lectures",
        "POST /v1/lectures",
        "PUT /lectures/v.mp4",
        f"POST /v1/lectures/{LECTURE_ID}/complete-upload",
        f"POST /v1/lectures/{LECTURE_ID}/process",
        *[f"GET /v1/lectures/{LECTURE_ID}"] * 3,
        f"GET /v1/lectures/{LECTURE_ID}/runs",
    ]


def test_a_processed_lecture_is_used_as_it_is(tmp_path: Path) -> None:
    ready = {
        "id": str(LECTURE_ID),
        "content_hash": LECTURE.video_sha256,
        "status": "ready",
        "updated_at": "2026-09-30T00:00:00Z",
    }
    client, calls = _api([], lectures=[ready])

    assert prepare.process(client, LECTURE, tmp_path / "v.mp4", poll_s=0) == LECTURE_ID
    assert calls == ["GET /v1/lectures"]


def test_failed_processing_says_why(tmp_path: Path) -> None:
    (tmp_path / "v.mp4").write_bytes(VIDEO)
    client, _ = _api(["failed"], lectures=[], error="speech model missing")

    with pytest.raises(RuntimeError, match="speech model missing"):
        prepare.process(client, LECTURE, tmp_path / "v.mp4", poll_s=0)


def test_bounds_for_search_modes_not_run_are_left_out_of_the_gate() -> None:
    thresholds = {
        "retrieval": {"rerank.recall_at_5": Bound(min=0.95), "hybrid.recall_at_5": Bound(min=0.9)},
        "asr": {"wer": Bound(max=0.05)},
    }

    assert _unmeasured(thresholds, ["dense", "bm25", "hybrid"]) == ["rerank.recall_at_5"]
    assert thresholds == {
        "retrieval": {"hybrid.recall_at_5": Bound(min=0.9)},
        "asr": {"wer": Bound(max=0.05)},
    }
