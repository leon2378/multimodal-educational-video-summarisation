"""Steps the integration tests share: upload a lecture, wait for it, collect a streamed answer."""

import json
import time
from typing import Any

import httpx2
from fastapi.testclient import TestClient


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


def ask(
    client: TestClient, path: str, question: str, thread_id: str | None = None
) -> list[dict[str, Any]]:
    """POST a question to `path` (a lecture's or course's /ask) and collect the events."""
    body = {"question": question} | ({"thread_id": thread_id} if thread_id else {})
    events = []
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events
