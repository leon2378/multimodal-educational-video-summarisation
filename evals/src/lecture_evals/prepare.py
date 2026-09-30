"""Getting a fresh stack ready to be scored, as the eval gate in CI does (`lecture-eval --prepare`):
the datasets' media downloaded where missing and checked against their hashes, then each
dataset's lecture uploaded and processed through the API, unless it already is.
"""

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lecture_evals.client import find_lecture, get
from lecture_evals.golden import GoldenLecture
from lecture_evals.runs import file_sha256


@dataclass(frozen=True)
class MediaFile:
    path: Path
    sha256: str
    url: str | None


def fetch(files: Iterable[MediaFile], client: httpx.Client) -> None:
    """Download each file that's missing; every file must match its dataset's hash."""
    for media in files:
        downloaded = not media.path.is_file()
        if downloaded:
            if media.url is None:
                raise LookupError(f"missing {media.path}, and its dataset gives no URL")
            _download(client, media.url, media.path)
        if file_sha256(media.path) != media.sha256:
            if downloaded:
                media.path.unlink()  # not kept, so the next run downloads it again
            raise LookupError(f"{media.path} doesn't match its dataset's SHA-256")


def _download(client: httpx.Client, url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
    partial.replace(path)
    print(f"Downloaded {path.name} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)


def process(
    client: httpx.Client,
    lecture: GoldenLecture,
    video: Path,
    timeout_s: float = 7200,
    poll_s: float = 15,
) -> uuid.UUID:
    """The dataset's lecture, processed: found if it already is, else uploaded and processed."""
    try:
        return find_lecture(client, lecture)
    except LookupError:
        pass
    created = client.post(
        "/v1/lectures",
        json={
            "title": lecture.title,
            "filename": lecture.video,
            "content_type": "video/mp4",
            "licence": lecture.licence,
            "attribution": lecture.attribution,
        },
    )
    created.raise_for_status()
    body: dict[str, Any] = created.json()
    lecture_id = uuid.UUID(body["lecture"]["id"])
    upload = body["upload"]
    # Straight to storage: the presigned URL is absolute, so the client's base URL doesn't apply.
    client.put(
        upload["url"], content=video.read_bytes(), headers=upload["headers"]
    ).raise_for_status()
    client.post(f"/v1/lectures/{lecture_id}/complete-upload").raise_for_status()
    client.post(f"/v1/lectures/{lecture_id}/process").raise_for_status()
    print(f"Processing {lecture.title} ({lecture_id})", flush=True)

    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        status = get(client, f"/v1/lectures/{lecture_id}")["status"]
        if status in ("ready", "failed"):
            runs: list[dict[str, Any]] = get(client, f"/v1/lectures/{lecture_id}/runs")
            run = runs[0] if runs else {}
            stages = ", ".join(
                f"{s['stage']} {s['seconds']:.0f} s{' (cached)' if s['cached'] else ''}"
                for s in run.get("stages", [])
            )
            print(f"{status} after {time.monotonic() - started:.0f} s: {stages}", flush=True)
            if status == "failed":
                raise RuntimeError(f"processing {lecture.title} failed: {run.get('error')}")
            return lecture_id
        time.sleep(poll_s)
    raise TimeoutError(f"{lecture.title} wasn't processed within {timeout_s:.0f} s")
