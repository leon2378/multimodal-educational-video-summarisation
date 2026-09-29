"""Phase 3: courses. Group lectures, then search and ask across them."""

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.helpers import ask
from tests.unit.fakes import FakeQA

pytestmark = pytest.mark.integration


def test_search_and_ask_across_a_course(
    processing_client: TestClient, processed_lecture: str, fake_qa: FakeQA
) -> None:
    client = processing_client
    course = client.post("/v1/courses", json={"title": "Algorithms"}).json()
    course_id = course["id"]
    assert course["lecture_count"] == 0

    # Nothing in the course yet: nothing to find, nothing to answer from.
    empty = client.get("/v1/search", params={"q": "memoisation", "course_id": course_id})
    assert empty.json()["hits"] == []
    no_lectures = client.post(f"/v1/courses/{course_id}/ask", json={"question": "Why?"})
    assert no_lectures.status_code == 409

    moved = client.patch(f"/v1/lectures/{processed_lecture}", json={"course_id": course_id})
    assert moved.status_code == 200, moved.text
    assert moved.json()["course_id"] == course_id
    detail = client.get(f"/v1/courses/{course_id}").json()
    assert ([lecture["id"] for lecture in detail["lectures"]], detail["lecture_count"]) == (
        [processed_lecture],
        1,
    )
    counts = {c["id"]: c["lecture_count"] for c in client.get("/v1/courses").json()}
    assert counts[course_id] == 1

    search = client.get("/v1/search", params={"q": "memoisation", "course_id": course_id})
    hits = search.json()["hits"]
    assert hits
    assert {hit["lecture_id"] for hit in hits} == {processed_lecture}

    events = ask(client, f"/v1/courses/{course_id}/ask", "What does memoisation store?")

    assert events[-1]["type"] == "done"
    assert fake_qa.instructions[-1].startswith("You answer a student's question about a course")
    sources = events[1]["sources"]
    assert {(s["lecture_label"], s["lecture_title"]) for s in sources} == {("L1", "Processed")}
    grounded, made_up = events[-1]["answer"]["citations"]
    assert grounded["label"].startswith("[L1 ")
    assert (grounded["valid"], grounded["lecture_id"]) == (True, processed_lecture)
    assert (made_up["label"], made_up["valid"]) == ("[59:59]", False)

    thread_id = events[0]["thread_id"]
    threads = client.get(f"/v1/courses/{course_id}/threads").json()
    assert [t["id"] for t in threads] == [thread_id]
    assert (threads[0]["course_id"], threads[0]["lecture_id"]) == (course_id, None)
    assert client.get(f"/v1/lectures/{processed_lecture}/threads").json() == []
    # A course's thread can't be continued as one about the lecture.
    elsewhere = {"question": "And?", "thread_id": thread_id}
    assert client.post(f"/v1/lectures/{processed_lecture}/ask", json=elsewhere).status_code == 404
    follow_up = ask(client, f"/v1/courses/{course_id}/ask", "Why?", thread_id)
    assert follow_up[1]["search_query"] == "Why? (standalone)"

    # Deleting the course keeps its lecture and removes its conversations.
    assert client.delete(f"/v1/courses/{course_id}").status_code == 204
    assert client.get(f"/v1/lectures/{processed_lecture}").json()["course_id"] is None
    assert client.get(f"/v1/threads/{thread_id}").status_code == 404
    assert client.get(f"/v1/courses/{course_id}").status_code == 404


def test_course_requests_are_checked(processing_client: TestClient, processed_lecture: str) -> None:
    client = processing_client
    unknown = str(uuid.uuid4())
    course_id = client.post("/v1/courses", json={"title": "Checked"}).json()["id"]

    assert client.post("/v1/courses", json={"title": "   "}).status_code == 422
    missing_course = {"course_id": unknown}
    assert client.patch(f"/v1/lectures/{processed_lecture}", json=missing_course).status_code == 422
    new_lecture = {
        "title": "T",
        "filename": "t.mp4",
        "content_type": "video/mp4",
        "course_id": unknown,
    }
    assert client.post("/v1/lectures", json=new_lecture).status_code == 422
    # Sending no fields changes nothing.
    unchanged = client.patch(f"/v1/lectures/{processed_lecture}", json={}).json()
    assert unchanged["course_id"] is None
    both = {"q": "x", "course_id": course_id, "lecture_id": processed_lecture}
    assert client.get("/v1/search", params=both).status_code == 422
    assert client.get("/v1/search", params={"q": "x", "course_id": unknown}).status_code == 404
    assert client.post(f"/v1/courses/{unknown}/ask", json={"question": "Why?"}).status_code == 404
