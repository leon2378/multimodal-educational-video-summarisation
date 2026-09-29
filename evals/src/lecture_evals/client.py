"""The eval suites call the running API like any client, so what they score is what users get."""

import json
import uuid
from typing import Any

import httpx

from lecture_evals.golden import GoldenLecture


def get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def find_lecture(client: httpx.Client, lecture: GoldenLecture) -> uuid.UUID:
    """The most recently processed lecture made from the dataset's video, found by its hash."""
    lectures: list[dict[str, Any]] = get(client, "/v1/lectures")
    matches = [
        found
        for found in lectures
        if found["content_hash"] == lecture.video_sha256 and found["status"] == "ready"
    ]
    if not matches:
        raise LookupError(f"no processed lecture has the video {lecture.video}: process it first")
    return uuid.UUID(max(matches, key=lambda found: found["updated_at"])["id"])


def ask(client: httpx.Client, lecture_id: uuid.UUID, question: str) -> list[dict[str, Any]]:
    """Ask in a new thread and collect the answer's server-sent events."""
    events = []
    with client.stream(
        "POST", f"/v1/lectures/{lecture_id}/ask", json={"question": question}
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def delete_thread(client: httpx.Client, thread_id: str) -> None:
    client.delete(f"/v1/threads/{thread_id}").raise_for_status()
