import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from lecture_api.demo import Manifest, export

VIDEO = b"a lecture's video"
SHA256 = hashlib.sha256(VIDEO).hexdigest()


def _lecture(title: str, **fields: object) -> dict[str, object]:
    return {
        "id": title,
        "title": title,
        "status": "ready",
        "visibility": "public",
        "content_hash": SHA256,
        "source_filename": "Lecture.MP4",
        "content_type": "video/mp4",
        "licence": "CC BY-NC-SA 4.0",
        "attribution": "MIT OpenCourseWare",
        "course_id": "c1",
        "created_at": "2026-09-29T10:00:00Z",
    } | fields


class FakeStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get_bytes(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def copy(self, source: str, destination: str) -> None:
        self.objects[destination] = self.objects[source]

    def list_keys(self, prefix: str) -> Iterator[str]:
        yield from (key for key in self.objects if key.startswith(prefix))

    def download_file(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])


def _api(lectures: list[dict[str, object]], video: bytes = VIDEO) -> httpx.Client:
    courses = [
        {"id": "c1", "title": "6.0001", "description": "MIT", "visibility": "public"},
        {"id": "c2", "title": "Empty", "description": None, "visibility": "public"},
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "storage":
            return httpx.Response(200, content=video)
        if request.url.path == "/v1/lectures":
            return httpx.Response(200, json=lectures)
        if request.url.path == "/v1/courses":
            return httpx.Response(200, json=courses)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"url": "http://storage/video"})
        return httpx.Response(404)

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond))


def test_export_writes_the_public_ready_lectures_and_the_stage_cache(tmp_path: Path) -> None:
    lectures = [
        _lecture("Sorting", created_at="2026-09-30T10:00:00Z"),
        _lecture("Recursion"),
        _lecture("Private", visibility="private"),
        _lecture("Half done", status="processing"),
    ]
    store = FakeStore({"artifacts/asr/k.json": b"{}", "raw/x/source.mp4": b"not exported"})
    with _api(lectures) as api:
        manifest = export(api, store, tmp_path, api)

    # Oldest first, as they were added.
    assert [lecture.title for lecture in manifest.lectures] == ["Recursion", "Sorting"]
    lecture = manifest.lectures[0]
    assert lecture.video == f"videos/{SHA256}.mp4"
    assert (lecture.course, lecture.licence) == ("6.0001", "CC BY-NC-SA 4.0")
    assert [course.title for course in manifest.courses] == ["6.0001"]
    assert (tmp_path / "demo" / lecture.video).read_bytes() == VIDEO
    saved = Manifest.model_validate(json.loads((tmp_path / "demo/lectures.json").read_text()))
    assert saved == manifest
    assert (tmp_path / "artifacts/asr/k.json").is_file()
    assert not (tmp_path / "raw").exists()


def test_export_refuses_a_video_that_doesnt_match_its_hash(tmp_path: Path) -> None:
    with (
        _api([_lecture("Recursion")], video=b"something else") as api,
        pytest.raises(ValueError, match="doesn't match"),
    ):
        export(api, FakeStore({}), tmp_path, api)
