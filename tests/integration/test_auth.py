"""Sign-in, ownership and quotas through the API, on real Postgres and storage.

The issuer is faked: a bearer token `as:<subject>` is that user (the real token checks are in
tests/unit/test_auth.py). Subjects are unique to each test, so quotas start from zero.
"""

import asyncio
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from lecture_api import quotas
from lecture_api.auth import Viewer, get_verifier
from lecture_api.deps import get_searcher, get_temporal
from lecture_api.main import create_app
from lecture_core.db import create_engine, create_sessionmaker
from lecture_core.models import Lecture, LectureStatus, QAThread, UsageKind, User
from lecture_core.processing import ProcessInput
from lecture_core.settings import Settings

pytestmark = pytest.mark.integration


class FakeIssuer:
    def claims(self, token: str) -> dict[str, Any]:
        if not token.startswith("as:"):
            raise jwt.InvalidTokenError("not a test token")
        return {"sub": token.removeprefix("as:")}


@pytest.fixture
def signed(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"auth_issuer": "https://issuer.test", "admin_users": ["admin"]}
    )


@pytest.fixture
def api(signed: Settings) -> Iterator[TestClient]:
    app = create_app(signed.model_copy(update={"quota_uploads_per_day": 2}))
    app.dependency_overrides[get_verifier] = FakeIssuer
    # POST /process connects to Temporal before checking the lecture. None runs here, and the
    # requests that get that far are refused, so a stand-in lets them reach the check.
    app.dependency_overrides[get_temporal] = lambda: None
    with TestClient(app) as client:
        yield client


def who(name: str) -> str:
    return f"{name}-{uuid.uuid4().hex[:8]}"


def as_(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer as:{subject}"}


def new_lecture(api: TestClient, subject: str, title: str = "Recursion") -> dict[str, Any]:
    response = api.post(
        "/v1/lectures",
        json={"title": title, "filename": "l.mp4", "content_type": "video/mp4"},
        headers=as_(subject),
    )
    assert response.status_code == 201, response.text
    lecture: dict[str, Any] = response.json()["lecture"]
    return lecture


def test_anonymous_visitors_read_the_public_lectures_and_nothing_else(api: TestClient) -> None:
    alice = who("alice")
    demo = new_lecture(api, "admin", "Demo")
    opened = api.patch(
        f"/v1/lectures/{demo['id']}", json={"visibility": "public"}, headers=as_("admin")
    )
    assert opened.status_code == 200
    assert opened.json()["visibility"] == "public"
    private = new_lecture(api, alice)

    listed = {lecture["id"] for lecture in api.get("/v1/lectures").json()}
    assert demo["id"] in listed
    assert private["id"] not in listed
    assert api.get(f"/v1/lectures/{demo['id']}").status_code == 200
    assert api.get(f"/v1/lectures/{private['id']}").status_code == 404
    refused = api.post(
        "/v1/lectures", json={"title": "x", "filename": "x.mp4", "content_type": "video/mp4"}
    )
    assert refused.status_code == 401
    assert refused.headers["WWW-Authenticate"] == "Bearer"
    # Sign-in is checked first: this API has no language model, which would be 503.
    asked = api.post(f"/v1/lectures/{demo['id']}/ask", json={"question": "Why?"})
    assert asked.status_code == 401
    assert api.get("/v1/me").json() == {
        "auth": True,
        "signed_in": False,
        "user": None,
        "admin": False,
        "quotas": None,
    }


def test_a_lecture_belongs_to_whoever_uploaded_it(api: TestClient) -> None:
    alice, bob = who("alice"), who("bob")
    lecture = new_lecture(api, alice)
    me = api.get("/v1/me", headers=as_(alice)).json()
    assert (lecture["visibility"], lecture["owner_id"]) == ("private", me["user"]["id"])
    path = f"/v1/lectures/{lecture['id']}"

    assert api.get(path, headers=as_(alice)).status_code == 200
    assert api.get(path, headers=as_(bob)).status_code == 404
    assert api.post(f"{path}/process", headers=as_(bob)).status_code == 404
    parts = api.post(f"{path}/upload-parts", json={"size_bytes": 10}, headers=as_(bob))
    assert parts.status_code == 404
    assert api.patch(path, json={"course_id": None}, headers=as_(bob)).status_code == 404
    # Only admins publish.
    assert api.patch(path, json={"visibility": "public"}, headers=as_(alice)).status_code == 403
    assert api.get(path, headers=as_("admin")).status_code == 200
    assert api.patch(path, json={"visibility": "public"}, headers=as_("admin")).status_code == 200
    # Bob can read it now, still not change it.
    assert api.get(path, headers=as_(bob)).status_code == 200
    assert api.patch(path, json={"course_id": None}, headers=as_(bob)).status_code == 403


def test_courses_and_search_cover_only_what_the_caller_may_read(api: TestClient) -> None:
    alice, bob = who("alice"), who("bob")
    course = api.post("/v1/courses", json={"title": "Alice's"}, headers=as_(alice)).json()
    assert course["visibility"] == "private"
    assert api.get(f"/v1/courses/{course['id']}", headers=as_(bob)).status_code == 404
    assert course["id"] not in {c["id"] for c in api.get("/v1/courses", headers=as_(bob)).json()}
    # Nobody else's course takes your lecture, and only admins make public courses.
    lecture = new_lecture(api, bob)
    moved = api.patch(
        f"/v1/lectures/{lecture['id']}", json={"course_id": course["id"]}, headers=as_(bob)
    )
    assert moved.status_code == 422  # as if it didn't exist
    public = api.post("/v1/courses", json={"title": "x", "visibility": "public"}, headers=as_(bob))
    assert public.status_code == 403
    # Searching someone else's lecture is like searching one that doesn't exist.
    found = api.get(
        "/v1/search", params={"q": "x", "lecture_id": lecture["id"]}, headers=as_(alice)
    )
    assert found.status_code == 404


def test_uploads_are_limited_per_day_except_for_admins(api: TestClient) -> None:
    alice = who("alice")
    new_lecture(api, alice)
    new_lecture(api, alice)
    third = api.post(
        "/v1/lectures",
        json={"title": "x", "filename": "x.mp4", "content_type": "video/mp4"},
        headers=as_(alice),
    )
    assert third.status_code == 429
    assert "2 lectures today" in third.json()["detail"]
    assert 0 < int(third.headers["Retry-After"]) <= 86_400
    usage = api.get("/v1/me", headers=as_(alice)).json()["quotas"]
    assert (usage["uploads_today"], usage["uploads_per_day"]) == (2, 2)
    for _ in range(3):
        new_lecture(api, "admin")


def test_a_file_over_the_size_limit_doesnt_use_up_an_upload(api: TestClient) -> None:
    alice = who("alice")
    limit = api.get("/v1/me", headers=as_(alice)).json()["quotas"]["upload_bytes"]

    too_big = api.post(
        "/v1/lectures",
        json={
            "title": "x",
            "filename": "x.mp4",
            "content_type": "video/mp4",
            "size_bytes": limit + 1,
        },
        headers=as_(alice),
    )

    assert too_big.status_code == 413
    assert api.get("/v1/me", headers=as_(alice)).json()["quotas"]["uploads_today"] == 0


class _Index:
    """Stands in for the search index, which isn't running here: records what's dropped."""

    def __init__(self) -> None:
        self.deleted: list[uuid.UUID] = []

    def delete_lecture(self, lecture_id: uuid.UUID) -> None:
        self.deleted.append(lecture_id)


def _without_search(api: TestClient) -> _Index:
    index = _Index()
    app = cast(FastAPI, api.app)
    app.dependency_overrides[get_searcher] = lambda: SimpleNamespace(index=index)
    return index


def test_its_owner_deletes_a_lecture_and_gets_no_upload_back(api: TestClient) -> None:
    index = _without_search(api)
    alice, bob = who("alice"), who("bob")
    first, second = new_lecture(api, alice), new_lecture(api, alice)
    path = f"/v1/lectures/{first['id']}"

    assert api.delete(path).status_code == 401
    assert api.delete(path, headers=as_(bob)).status_code == 404  # private, so not even seen
    assert api.delete(path, headers=as_(alice)).status_code == 204
    assert api.get(path, headers=as_(alice)).status_code == 404
    assert index.deleted == [uuid.UUID(first["id"])]
    # The day's two uploads still count: deleting one doesn't make room for another.
    again = api.post(
        "/v1/lectures",
        json={"title": "x", "filename": "x.mp4", "content_type": "video/mp4"},
        headers=as_(alice),
    )
    assert again.status_code == 429
    assert api.get("/v1/me", headers=as_(alice)).json()["quotas"]["uploads_today"] == 2
    assert api.delete(f"/v1/lectures/{second['id']}", headers=as_("admin")).status_code == 204


def test_a_lecture_isnt_deleted_while_it_processes(api: TestClient, signed: Settings) -> None:
    _without_search(api)
    alice = who("alice")
    lecture = new_lecture(api, alice)
    asyncio.run(_set_status(signed, uuid.UUID(lecture["id"]), LectureStatus.PROCESSING))

    response = api.delete(f"/v1/lectures/{lecture['id']}", headers=as_(alice))

    assert response.status_code == 409
    assert "being processed" in response.json()["detail"]


def test_bad_tokens_are_refused(api: TestClient) -> None:
    assert api.get("/v1/lectures", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert api.get("/v1/lectures", headers={"Authorization": "Basic abc"}).status_code == 401


def test_conversations_are_private_to_their_user(api: TestClient, signed: Settings) -> None:
    alice, bob = who("alice"), who("bob")
    lecture = new_lecture(api, "admin")
    api.patch(f"/v1/lectures/{lecture['id']}", json={"visibility": "public"}, headers=as_("admin"))
    api.get("/v1/me", headers=as_(alice))
    api.get("/v1/me", headers=as_(bob))
    threads = asyncio.run(_threads(signed, uuid.UUID(lecture["id"]), {alice: "a", bob: "b"}))
    path = f"/v1/lectures/{lecture['id']}/threads"

    assert [t["title"] for t in api.get(path, headers=as_(alice)).json()] == ["a"]
    assert [t["title"] for t in api.get(path, headers=as_(bob)).json()] == ["b"]
    assert api.get(path).status_code == 401
    assert api.get(f"/v1/threads/{threads[alice]}", headers=as_(bob)).status_code == 404
    assert api.delete(f"/v1/threads/{threads[alice]}", headers=as_(bob)).status_code == 404
    assert api.get(f"/v1/threads/{threads[alice]}", headers=as_(alice)).status_code == 200


def test_questions_are_limited_and_the_daily_budget_pauses_everyone(signed: Settings) -> None:
    async def check() -> None:
        engine = create_engine(signed)
        try:
            async with create_sessionmaker(engine)() as session:
                alice = User(subject=who("alice"))
                session.add(alice)
                await session.flush()
                # Two questions just now, as the ledger records them when they're asked.
                quotas.record(session, UsageKind.QUESTION, alice.id)
                quotas.record(session, UsageKind.QUESTION, alice.id)
                # An answer that cost $0.30: a million input tokens at $0.30 a million.
                used = {"requests": 1, "input_tokens": 1_000_000, "output_tokens": 0}
                cost = quotas.answer_cost("google:gemini-3.5-flash-lite", used)
                assert cost == pytest.approx(0.30)
                quotas.record(session, UsageKind.LLM, alice.id, cost)
                await session.commit()

                two_a_minute = signed.model_copy(update={"quota_questions_per_minute": 2})
                with pytest.raises(HTTPException) as limited:
                    await quotas.check_question(session, Viewer(user=alice), two_a_minute)
                assert limited.value.status_code == 429
                assert "questions in a minute" in str(limited.value.detail)

                broke = signed.model_copy(update={"daily_llm_budget_usd": 0.10})
                bob = Viewer(user=User(id=uuid.uuid4(), subject=who("bob")))
                with pytest.raises(HTTPException) as paused:
                    await quotas.check_question(session, bob, broke)
                assert "budget" in str(paused.value.detail)
                # Admins aren't limited.
                await quotas.check_question(session, Viewer(user=alice, admin=True), broke)
        finally:
            await engine.dispose()

    asyncio.run(check())


async def _threads(
    settings: Settings, lecture_id: uuid.UUID, titles: dict[str, str]
) -> dict[str, uuid.UUID]:
    """A conversation per user about the lecture, straight into the database."""
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session:
            made = {}
            for subject, title in titles.items():
                user = await session.scalar(select(User).where(User.subject == subject))
                assert user is not None
                thread = QAThread(lecture_id=lecture_id, user_id=user.id, title=title)
                session.add(thread)
                await session.flush()
                made[subject] = thread.id
            await session.commit()
            return made
    finally:
        await engine.dispose()


async def _set_status(settings: Settings, lecture_id: uuid.UUID, status: LectureStatus) -> None:
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            lecture = await session.get(Lecture, lecture_id)
            assert lecture is not None
            lecture.status = status
    finally:
        await engine.dispose()


class _Temporal:
    """Stands in for Temporal: records the workflows started."""

    def __init__(self) -> None:
        self.started: list[ProcessInput] = []

    async def start_workflow(self, workflow: str, request: ProcessInput, **_: object) -> None:
        self.started.append(request)


def test_a_lecture_from_a_link_counts_as_an_upload(api: TestClient, signed: Settings) -> None:
    temporal = _Temporal()
    # The client the API connects to on first use (lecture_api.deps.get_temporal).
    cast(FastAPI, api.app).state.temporal = temporal
    alice = who("alice")
    link = {"url": "https://example.com/talks/recursion.mp4"}

    for _ in range(2):
        created = api.post("/v1/lectures/from-url", json=link, headers=as_(alice))
        assert created.status_code == 201, created.text
    third = api.post("/v1/lectures/from-url", json=link, headers=as_(alice))

    assert third.status_code == 429
    lecture = created.json()
    assert (lecture["title"], lecture["source_filename"]) == (
        "Video from example.com",
        "recursion.mp4",
    )
    assert (lecture["status"], lecture["visibility"]) == ("processing", "private")
    started = temporal.started[-1]
    assert (started.source_url, started.title_from_source) == (link["url"], True)
    assert started.max_bytes == min(signed.quota_upload_bytes, signed.max_upload_bytes)
