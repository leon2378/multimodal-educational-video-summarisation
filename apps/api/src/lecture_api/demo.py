"""The demo's lectures (ADR 0010): exported once from a local stack, loaded on each deploy.

    lecture-demo export data/demo-export   # on your machine, with the local stack running
    lecture-demo load                      # on the demo's VM, in the API's container

`export` writes the local stack's public, processed lectures to a directory: their videos and a
manifest under demo/, and the whole stage cache under artifacts/, laid out as in the bucket.
Copied there (`make cloud-seed`), they let `load` make the lectures again through the API with
every stage coming from the cache: no speech recognition and no LLM calls. `load` runs while
sign-in is off, so what it makes is public (ADR 0009), and skips lectures that already exist.
"""

import argparse
import hashlib
import time
import uuid
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from lecture_core.settings import Settings
from lecture_core.storage import ObjectStorage, source_key

MANIFEST_KEY = "demo/lectures.json"


class DemoCourse(BaseModel):
    title: str
    description: str | None = None


class DemoLecture(BaseModel):
    title: str
    filename: str
    content_type: str
    licence: str | None = None
    attribution: str | None = None
    sha256: str
    # Under demo/ in the bucket.
    video: str
    course: str | None = None


class Manifest(BaseModel):
    courses: list[DemoCourse]
    lectures: list[DemoLecture]


class Response(Protocol):
    def json(self) -> Any: ...

    def raise_for_status(self) -> object: ...


class Api(Protocol):
    """An httpx client for the API, or FastAPI's TestClient."""

    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: Any = None) -> Response: ...


class Store(Protocol):
    def get_bytes(self, key: str) -> bytes | None: ...

    def copy(self, source: str, destination: str) -> None: ...

    def list_keys(self, prefix: str) -> Iterator[str]: ...

    def download_file(self, key: str, path: Path) -> None: ...


def export(api: Api, storage: Store, out: Path, http: httpx.Client) -> Manifest:
    """`http` fetches the videos from storage, through the URLs the API presigns."""
    # Oldest first, so they're made again in the order they were added.
    lectures = sorted(
        (
            lecture
            for lecture in _json(api.get("/v1/lectures"))
            if lecture["status"] == "ready" and lecture["visibility"] == "public"
        ),
        key=lambda lecture: str(lecture["created_at"]),
    )
    courses = {
        course["id"]: course
        for course in _json(api.get("/v1/courses"))
        if course["visibility"] == "public"
    }
    entries = []
    for lecture in lectures:
        sha256 = lecture["content_hash"]
        suffix = PurePosixPath(lecture["source_filename"]).suffix.lower()
        video = f"videos/{sha256}{suffix}"
        path = out / "demo" / video
        if not (path.is_file() and _sha256(path) == sha256):
            url = _json(api.get(f"/v1/lectures/{lecture['id']}/media"))["url"]
            _download(http, url, path)
            if _sha256(path) != sha256:
                raise ValueError(f"{lecture['title']}: the video doesn't match its SHA-256")
        course = courses.get(lecture["course_id"])
        entries.append(
            DemoLecture(
                title=lecture["title"],
                filename=lecture["source_filename"],
                content_type=lecture["content_type"],
                licence=lecture["licence"],
                attribution=lecture["attribution"],
                sha256=sha256,
                video=video,
                course=course["title"] if course else None,
            )
        )
    used = {entry.course for entry in entries}
    manifest = Manifest(
        courses=[
            DemoCourse(title=course["title"], description=course["description"])
            for course in courses.values()
            if course["title"] in used
        ],
        lectures=entries,
    )
    (out / MANIFEST_KEY).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for key in storage.list_keys("artifacts/"):
        if not (out / key).is_file():
            storage.download_file(key, out / key)
    return manifest


def load(api: Api, storage: Store, timeout_s: float = 30 * 60, poll_s: float = 5) -> list[str]:
    """Makes the manifest's courses and lectures, processes the lectures and waits for them,
    picking up where an earlier attempt stopped: a lecture that's ready is left alone, one whose
    video never arrived gets it, one that failed is processed again, and one still processing
    is waited for. Returns the titles of the lectures that weren't ready."""
    data = storage.get_bytes(MANIFEST_KEY)
    if data is None:
        raise LookupError(f"No {MANIFEST_KEY} in the bucket: run `make cloud-seed` first.")
    manifest = Manifest.model_validate_json(data)
    course_ids = {course["title"]: course["id"] for course in _json(api.get("/v1/courses"))}
    for course in manifest.courses:
        if course.title not in course_ids:
            made = _json(api.post("/v1/courses", json=course.model_dump()))
            course_ids[course.title] = made["id"]
    existing = {lecture["title"]: lecture for lecture in _json(api.get("/v1/lectures"))}
    pending: dict[str, str] = {}
    for entry in manifest.lectures:
        lecture = existing.get(entry.title)
        if lecture is not None and lecture["status"] == "ready":
            continue
        if lecture is None:
            lecture = _json(
                api.post(
                    "/v1/lectures",
                    json={
                        "title": entry.title,
                        "filename": entry.filename,
                        "content_type": entry.content_type,
                        "licence": entry.licence,
                        "attribution": entry.attribution,
                        "course_id": course_ids.get(entry.course) if entry.course else None,
                    },
                )
            )["lecture"]
        if lecture["status"] == "awaiting_upload":
            # Where the API expects the upload; the store copies the video there itself.
            key = source_key(uuid.UUID(lecture["id"]), entry.filename)
            storage.copy(f"demo/{entry.video}", key)
            _json(api.post(f"/v1/lectures/{lecture['id']}/complete-upload"))
        if lecture["status"] != "processing":
            _json(api.post(f"/v1/lectures/{lecture['id']}/process"))
        pending[lecture["id"]] = entry.title
        print(f"Processing {entry.title}", flush=True)
    made = list(pending.values())
    deadline = time.monotonic() + timeout_s
    while pending:
        for lecture_id, title in list(pending.items()):
            status = _json(api.get(f"/v1/lectures/{lecture_id}"))["status"]
            if status == "failed":
                raise RuntimeError(f"{title} failed to process: see its runs")
            if status == "ready":
                print(f"Ready: {title}", flush=True)
                del pending[lecture_id]
        if pending:
            if time.monotonic() > deadline:
                titles = ", ".join(pending.values())
                raise TimeoutError(f"Still processing after {timeout_s:.0f} s: {titles}")
            time.sleep(poll_s)
    return made


def _json(response: Response) -> Any:
    response.raise_for_status()
    return response.json()


def _download(http: httpx.Client, url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with http.stream("GET", url) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
    partial.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lecture-demo", description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://localhost:8000", help="the API's address")
    commands = parser.add_subparsers(dest="command", required=True)
    export_command = commands.add_parser("export", help="write the local stack's demo lectures")
    export_command.add_argument("out", type=Path, help="directory, laid out as the bucket")
    load_command = commands.add_parser("load", help="make the demo lectures through the API")
    load_command.add_argument("--timeout", type=float, default=30 * 60, help="seconds")
    args = parser.parse_args(argv)

    storage = ObjectStorage(Settings())
    with httpx.Client(base_url=args.api, timeout=120) as client:
        if args.command == "export":
            manifest = export(client, storage, args.out, client)
            print(f"Exported {len(manifest.lectures)} lectures to {args.out}")
        else:
            made = load(client, storage, args.timeout)
            print(f"Loaded {len(made)} lectures")


if __name__ == "__main__":
    main()
