"""Loading the demo's lectures (lecture_api.demo) through the API, on real storage and workers."""

import hashlib
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lecture_api.demo import MANIFEST_KEY, DemoCourse, DemoLecture, Manifest, load
from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage

pytestmark = pytest.mark.integration


def test_the_demo_lectures_load_into_their_course_picking_up_where_a_load_stopped(
    processing_client: TestClient, processing_settings: Settings, synthetic_video: Path
) -> None:
    storage = ObjectStorage(processing_settings)
    data = synthetic_video.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    suffix = uuid.uuid4().hex[:6]
    course, first, second = f"Course {suffix}", f"Lecture {suffix}", f"Interrupted {suffix}"
    storage.put_bytes(f"demo/videos/{sha256}.mp4", data, "video/mp4")
    manifest = Manifest(
        courses=[DemoCourse(title=course, description="From the demo set")],
        lectures=[
            DemoLecture(
                title=title,
                filename="lecture.mp4",
                content_type="video/mp4",
                licence="CC BY-NC-SA 4.0",
                sha256=sha256,
                video=f"videos/{sha256}.mp4",
                course=course,
            )
            for title in (first, second)
        ],
    )
    storage.put_bytes(MANIFEST_KEY, manifest.model_dump_json().encode(), "application/json")
    # An earlier load stopped after making this lecture, before its video was in place.
    interrupted = processing_client.post(
        "/v1/lectures",
        json={"title": second, "filename": "lecture.mp4", "content_type": "video/mp4"},
    )
    assert interrupted.status_code == 201

    assert load(processing_client, storage, timeout_s=120, poll_s=0.5) == [first, second]
    # A second deploy's load finds them ready.
    assert load(processing_client, storage, timeout_s=120, poll_s=0.5) == []

    lectures = {x["title"]: x for x in processing_client.get("/v1/lectures").json()}
    for title in (first, second):
        lecture = lectures[title]
        assert (lecture["status"], lecture["visibility"]) == ("ready", "public")
        assert lecture["content_hash"] == sha256
    assert lectures[first]["licence"] == "CC BY-NC-SA 4.0"
    detail = processing_client.get(f"/v1/courses/{lectures[first]['course_id']}").json()
    assert detail["title"] == course
    assert f"demo/videos/{sha256}.mp4" in list(storage.list_keys("demo/videos/"))
