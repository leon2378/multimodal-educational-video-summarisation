"""Phase 3b end to end: ask about a processed lecture, follow up, rate the answer.

The lecture is processed by the in-process workers, then answered by the fake Q&A model
(tests/unit/fakes.py), which cites the first sentence it's shown and one time outside every
passage.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.helpers import ask as ask_at
from tests.integration.helpers import upload_lecture
from tests.unit.fakes import FakeQA

pytestmark = pytest.mark.integration


def ask(
    client: TestClient, lecture_id: str, question: str, thread_id: str | None = None
) -> list[dict[str, Any]]:
    return ask_at(client, f"/v1/lectures/{lecture_id}/ask", question, thread_id)


def test_ask_follow_up_and_rate(processing_client: TestClient, processed_lecture: str) -> None:
    lecture_id = processed_lecture
    client = processing_client

    events = ask(client, lecture_id, "What does memoisation store?")

    assert [e["type"] for e in events[:2]] == ["start", "sources"]
    assert events[-1]["type"] == "done"
    thread_id = events[0]["thread_id"]
    assert events[0]["question"]["content"] == "What does memoisation store?"
    sources = events[1]["sources"]
    assert sources
    assert {source["lecture_id"] for source in sources} == {lecture_id}
    answer = events[-1]["answer"]
    assert answer["content"] == "".join(e["text"] for e in events if e["type"] == "delta")
    # The first citation copies a sentence time from the passages; [59:59] is past the end.
    grounded, made_up = answer["citations"]
    assert (grounded["valid"], grounded["lecture_id"]) == (True, lecture_id)
    assert grounded["segment_id"] in {source["segment_id"] for source in sources}
    assert made_up == {
        "label": "[59:59]",
        "at_s": 3599.0,
        "lecture_id": None,
        "segment_id": None,
        "valid": False,
    }
    assert answer["search_query"] == "What does memoisation store?"
    assert 0 < answer["first_token_ms"] <= answer["total_ms"]
    assert answer["model"] == "function:fake-qa"

    follow_up = ask(client, lecture_id, "Why does that help?", thread_id)

    assert follow_up[0]["thread_id"] == thread_id
    # Rewritten to stand on its own before searching.
    assert follow_up[1]["search_query"] == "Why does that help? (standalone)"

    thread = client.get(f"/v1/threads/{thread_id}").json()
    assert thread["title"] == "What does memoisation store?"
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"] * 2
    assert thread["messages"][1]["citations"] == answer["citations"]
    threads = client.get(f"/v1/lectures/{lecture_id}/threads").json()
    assert [t["id"] for t in threads] == [thread_id]

    answer_id = answer["id"]
    down = client.post(
        "/v1/feedback", json={"message_id": answer_id, "rating": "down", "reason": "Too short"}
    )
    assert down.status_code == 200, down.text
    assert (down.json()["rating"], down.json()["reason"]) == ("down", "Too short")
    up = client.post("/v1/feedback", json={"message_id": answer_id, "rating": "up"}).json()
    assert (up["rating"], up["reason"]) == ("up", None)  # replaces the earlier rating
    rated = client.get(f"/v1/threads/{thread_id}").json()["messages"][1]
    assert rated["feedback"]["rating"] == "up"
    question_id = thread["messages"][0]["id"]
    not_an_answer = {"message_id": question_id, "rating": "up"}
    assert client.post("/v1/feedback", json=not_an_answer).status_code == 404


def test_a_failed_answer_is_saved_and_reported(
    processing_client: TestClient, processed_lecture: str, fake_qa: FakeQA
) -> None:
    lecture_id = processed_lecture
    fake_qa.fail = True

    events = ask(processing_client, lecture_id, "What is memoisation?")

    assert events[-1]["type"] == "error"
    assert events[-1]["detail"] == "The language model is busy right now. Try again in a minute."
    failed = events[-1]["answer"]
    assert (failed["content"], failed["error"]) == ("", events[-1]["detail"])

    fake_qa.fail = False
    follow_up = ask(processing_client, lecture_id, "And then?", events[0]["thread_id"])
    # A failed answer isn't history: there was nothing to follow up on, so no rewrite.
    assert follow_up[1]["search_query"] == "And then?"
    assert follow_up[-1]["type"] == "done"


def test_asking_needs_a_processed_lecture_and_its_own_thread(
    processing_client: TestClient, processed_lecture: str, synthetic_video: Path
) -> None:
    lecture_id = processed_lecture
    client = processing_client
    unprocessed = upload_lecture(client, synthetic_video.read_bytes(), "Not processed yet")
    body = {"question": "Why?"}

    assert client.post(f"/v1/lectures/{unprocessed}/ask", json=body).status_code == 409
    other_thread = {"question": "Why?", "thread_id": str(uuid.uuid4())}
    assert client.post(f"/v1/lectures/{lecture_id}/ask", json=other_thread).status_code == 404
    assert client.get(f"/v1/threads/{uuid.uuid4()}").status_code == 404
