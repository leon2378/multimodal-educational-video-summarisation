"""Q&A over a lecture or a whole course (docs/blueprint.md, section 5).

POST /v1/lectures/{id}/ask      a streamed answer that cites the lecture as [mm:ss]
POST /v1/courses/{id}/ask       the same across a course's processed lectures, citing [L2 mm:ss]
GET  /v1/lectures/{id}/threads  a lecture's conversations, most recent first
GET  /v1/courses/{id}/threads   a course's conversations, most recent first
GET  /v1/threads/{id}           one conversation with its questions and answers
DELETE /v1/threads/{id}         delete a conversation, with its answers and ratings
POST /v1/feedback               thumbs up or down on an answer, with an optional reason

A follow-up question is first rewritten to stand on its own, then searched; the answer is
generated from the top segments only, and each citation is checked against them.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from lecture_api.deps import (
    AnswererDep,
    SearcherDep,
    SessionDep,
    SettingsDep,
    course_or_404,
    lecture_or_404,
)
from lecture_api.schemas import (
    AskDelta,
    AskDone,
    AskError,
    AskEvent,
    AskRequest,
    AskSources,
    AskStart,
    FeedbackIn,
    FeedbackOut,
    MessageOut,
    SourceOut,
    ThreadDetail,
    ThreadOut,
)
from lecture_core import metrics
from lecture_core.models import (
    Feedback,
    Lecture,
    LectureStatus,
    MessageRole,
    QAMessage,
    QAThread,
    SlideRow,
    TranscriptSegmentRow,
)
from lecture_core.qa import ChatTurn, Passage, Sentence, find_citations, label_lectures
from lecture_core.settings import Settings
from lecture_core.timeline import SlideReading
from lecture_llm.agents import Usage
from lecture_llm.qa import AnswerLLM
from lecture_llm.telemetry import record_usage
from lecture_rag.index import Hit
from lecture_rag.search import Searcher, SearchMode

router = APIRouter(tags=["qa"])
logger = logging.getLogger(__name__)

_SEARCH_ERRORS = (httpx.HTTPError, ResponseHandlingException, UnexpectedResponse)
# A sentence's first word can start a little before its segment's recorded start.
_SENTENCE_SLACK_S = 0.5
_ASK_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": AskEvent,
        "description": "Server-sent events. Each `data:` line is an AskEvent as JSON: "
        "start, sources, the answer in deltas, then done (or error).",
    }
}


@dataclass(frozen=True)
class _Scope:
    """What an answer draws on: one lecture, or the processed lectures of a course."""

    lecture_ids: list[uuid.UUID]
    titles: dict[uuid.UUID, str]
    course_id: uuid.UUID | None = None

    @property
    def lecture_id(self) -> uuid.UUID | None:
        return None if self.course_id else self.lecture_ids[0]

    @property
    def nothing_found(self) -> str:
        where = "course" if self.course_id else "lecture"
        return f"I couldn't find anything about that in this {where}."


@router.post(
    "/lectures/{lecture_id}/ask", response_class=StreamingResponse, responses=_ASK_RESPONSES
)
async def ask(
    lecture_id: uuid.UUID,
    body: AskRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    searcher: SearcherDep,
    answerer: AnswererDep,
) -> StreamingResponse:
    """Answer a question from the lecture, citing it as [mm:ss]. The question and answer are
    saved in a thread; send its `thread_id` to ask a follow-up. An answer the client
    disconnects from isn't saved."""
    lecture = await lecture_or_404(session, lecture_id)
    if lecture.status != LectureStatus.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "The lecture hasn't been processed yet.")
    scope = _Scope(lecture_ids=[lecture_id], titles={lecture_id: lecture.title})
    return await _ask(body, scope, request, session, settings, searcher, answerer)


@router.post("/courses/{course_id}/ask", response_class=StreamingResponse, responses=_ASK_RESPONSES)
async def ask_course(
    course_id: uuid.UUID,
    body: AskRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    searcher: SearcherDep,
    answerer: AnswererDep,
) -> StreamingResponse:
    """Answer a question from every processed lecture in the course. Citations name the
    lecture, like [L2 12:34]; each source's `lecture_label` says which lecture is L2."""
    await course_or_404(session, course_id)
    lectures = (
        await session.scalars(
            select(Lecture).where(
                Lecture.course_id == course_id, Lecture.status == LectureStatus.READY
            )
        )
    ).all()
    if not lectures:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "None of the course's lectures has been processed yet."
        )
    scope = _Scope(
        lecture_ids=[lecture.id for lecture in lectures],
        titles={lecture.id: lecture.title for lecture in lectures},
        course_id=course_id,
    )
    return await _ask(body, scope, request, session, settings, searcher, answerer)


@router.get("/lectures/{lecture_id}/threads")
async def list_threads(lecture_id: uuid.UUID, session: SessionDep) -> list[ThreadOut]:
    await lecture_or_404(session, lecture_id)
    return await _threads(session, QAThread.lecture_id == lecture_id)


@router.get("/courses/{course_id}/threads")
async def list_course_threads(course_id: uuid.UUID, session: SessionDep) -> list[ThreadOut]:
    await course_or_404(session, course_id)
    return await _threads(session, QAThread.course_id == course_id)


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: uuid.UUID, session: SessionDep) -> ThreadDetail:
    thread = await session.get(QAThread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
    messages = (
        await session.scalars(
            select(QAMessage).where(QAMessage.thread_id == thread_id).order_by(QAMessage.created_at)
        )
    ).all()
    ratings = {
        feedback.message_id: FeedbackOut.model_validate(feedback)
        for feedback in await session.scalars(
            select(Feedback).where(Feedback.message_id.in_([m.id for m in messages]))
        )
    }
    return ThreadDetail(
        **ThreadOut.model_validate(thread).model_dump(),
        messages=[
            MessageOut.model_validate(message).model_copy(
                update={"feedback": ratings.get(message.id)}
            )
            for message in messages
        ],
    )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: uuid.UUID, session: SessionDep) -> None:
    thread = await session.get(QAThread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
    await session.delete(thread)
    await session.commit()


@router.post("/feedback")
async def feedback(body: FeedbackIn, session: SessionDep) -> FeedbackOut:
    """Rate an answer. Rating it again replaces the earlier rating."""
    message = await session.get(QAMessage, body.message_id)
    if message is None or message.role != MessageRole.ASSISTANT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found.")
    upsert = (
        insert(Feedback)
        .values(message_id=body.message_id, rating=body.rating, reason=body.reason)
        .on_conflict_do_update(
            index_elements=[Feedback.message_id],
            set_={"rating": body.rating, "reason": body.reason, "updated_at": func.now()},
        )
        .returning(Feedback)
    )
    saved = await session.scalar(upsert, execution_options={"populate_existing": True})
    await session.commit()
    metrics.feedback.add(1, {"rating": body.rating.value})
    return FeedbackOut.model_validate(saved)


async def _ask(
    body: AskRequest,
    scope: _Scope,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    searcher: Searcher,
    answerer: AnswerLLM,
) -> StreamingResponse:
    """Save the question in its thread (a new one unless `thread_id` names one), then stream
    the answer."""
    history: list[ChatTurn] = []
    if body.thread_id is None:
        thread = QAThread(
            lecture_id=scope.lecture_id, course_id=scope.course_id, title=_title(body.question)
        )
        session.add(thread)
        await session.flush()
    else:
        found = await session.get(QAThread, body.thread_id)
        if found is None or (found.lecture_id, found.course_id) != (
            scope.lecture_id,
            scope.course_id,
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
        thread = found
        thread.updated_at = datetime.now(UTC)
        history = await _history(session, thread.id, settings.qa_history_turns)
    question = QAMessage(thread_id=thread.id, role=MessageRole.USER, content=body.question)
    session.add(question)
    await session.commit()
    await session.refresh(question)

    events = _answer(
        question=question,
        history=history,
        scope=scope,
        sessionmaker=request.app.state.sessionmaker,
        searcher=searcher,
        answerer=answerer,
        settings=settings,
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _threads(session: AsyncSession, where: ColumnElement[bool]) -> list[ThreadOut]:
    threads = await session.scalars(
        select(QAThread).where(where).order_by(QAThread.updated_at.desc()).limit(50)
    )
    return [ThreadOut.model_validate(thread) for thread in threads]


async def _answer(
    *,
    question: QAMessage,
    history: Sequence[ChatTurn],
    scope: _Scope,
    sessionmaker: async_sessionmaker[AsyncSession],
    searcher: Searcher,
    answerer: AnswerLLM,
    settings: Settings,
) -> AsyncIterator[str]:
    """The answer's events. The stream always ends with done or error, and the answer is saved
    either way."""
    started = time.monotonic()
    yield _sse(AskStart(thread_id=question.thread_id, question=MessageOut.model_validate(question)))
    answer = QAMessage(
        thread_id=question.thread_id,
        role=MessageRole.ASSISTANT,
        content="",
        model=answerer.model_name,
    )
    usage = Usage()
    parts: list[str] = []
    passages: list[Passage] = []
    failure: str | None = None
    try:
        query, used = await answerer.rewrite(question.content, history)
        usage = usage + used
        answer.search_query = query
        hits = await run_in_threadpool(
            searcher.search,
            query,
            lecture_ids=scope.lecture_ids,
            limit=settings.qa_passages,
            mode=SearchMode(settings.search_mode),
        )
        async with sessionmaker() as session:
            passages = await _passages(session, hits)
        if scope.course_id is not None:
            passages = label_lectures(passages, scope.titles)
        labels = {passage.lecture_id: passage.label for passage in passages}
        sources = [
            _source(hit, scope.titles.get(hit.lecture_id), labels.get(hit.lecture_id))
            for hit in hits
        ]
        answer.sources = [source.model_dump(mode="json") for source in sources]
        yield _sse(AskSources(search_query=query, sources=sources))
        deltas = (
            answerer.stream_answer(question.content, passages, history, usage)
            if passages
            else _just(scope.nothing_found)
        )
        async for delta in deltas:
            if answer.first_token_ms is None:
                answer.first_token_ms = _ms_since(started)
            parts.append(delta)
            yield _sse(AskDelta(text=delta))
    except _SEARCH_ERRORS:
        logger.exception("search failed")
        failure = "Search is unavailable right now."
    except Exception as error:  # the model: the stream must still end, and say why
        logger.exception("answer failed")
        failure = _model_failure(error)
    answer.error = failure
    answer.content = "".join(parts)
    answer.citations = [c.model_dump(mode="json") for c in find_citations(answer.content, passages)]
    answer.usage = usage.model_dump()
    answer.total_ms = _ms_since(started)
    record_usage(usage, answerer.model_name, "qa")
    outcome = {
        "scope": "course" if scope.course_id else "lecture",
        "outcome": "error" if failure else "done",
    }
    metrics.qa_answers.add(1, outcome)
    metrics.qa_duration.record(answer.total_ms, outcome)
    if answer.first_token_ms is not None:
        metrics.qa_first_token.record(answer.first_token_ms, outcome)
    async with sessionmaker() as session, session.begin():
        session.add(answer)
        thread = await session.get(QAThread, question.thread_id)
        if thread is not None:
            thread.updated_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(answer)
    saved = MessageOut.model_validate(answer)
    yield _sse(AskError(detail=failure, answer=saved) if failure else AskDone(answer=saved))


async def _passages(session: AsyncSession, hits: Sequence[Hit]) -> list[Passage]:
    """The retrieved segments with their sentences and slides, from the lectures' results."""
    if not hits:
        return []
    sentences = (
        await session.scalars(
            select(TranscriptSegmentRow)
            .where(
                or_(
                    *(
                        and_(
                            TranscriptSegmentRow.lecture_id == hit.lecture_id,
                            TranscriptSegmentRow.start_s >= hit.start_s - _SENTENCE_SLACK_S,
                            TranscriptSegmentRow.start_s < hit.end_s,
                        )
                        for hit in hits
                    )
                )
            )
            .order_by(TranscriptSegmentRow.start_s)
        )
    ).all()
    wanted = {(hit.lecture_id, hit.slide_id) for hit in hits if hit.slide_id is not None}
    slides: dict[tuple[uuid.UUID, int], SlideReading] = {}
    if wanted:
        for row in await session.scalars(
            select(SlideRow).where(
                or_(
                    *(
                        and_(SlideRow.lecture_id == lid, SlideRow.slide_id == sid)
                        for lid, sid in wanted
                    )
                )
            )
        ):
            slides[(row.lecture_id, row.slide_id)] = SlideReading(
                slide_id=row.slide_id,
                title=row.title,
                text=row.text,
                figure_description=row.figure_description,
                latex=row.latex,
                code=row.code,
            )
    passages = []
    for hit in hits:
        said = [
            Sentence(start_s=row.start_s, text=row.text)
            for row in sentences
            if row.lecture_id == hit.lecture_id
            and hit.start_s - _SENTENCE_SLACK_S <= row.start_s < hit.end_s
        ]
        passages.append(
            Passage(
                lecture_id=hit.lecture_id,
                segment_id=hit.segment_id,
                start_s=hit.start_s,
                end_s=hit.end_s,
                chapter=hit.chapter,
                slide=slides.get((hit.lecture_id, hit.slide_id))
                if hit.slide_id is not None
                else None,
                # Results from before sentence-level transcripts: the segment as one line.
                sentences=said or [Sentence(start_s=hit.start_s, text=hit.transcript)],
            )
        )
    return passages


async def _history(session: AsyncSession, thread_id: uuid.UUID, turns: int) -> list[ChatTurn]:
    """The thread's last `turns` answered questions, oldest first."""
    messages = await session.scalars(
        select(QAMessage).where(QAMessage.thread_id == thread_id).order_by(QAMessage.created_at)
    )
    history: list[ChatTurn] = []
    asked: str | None = None
    for message in messages:
        if message.role == MessageRole.USER:
            asked = message.content
        elif asked is not None and message.error is None and message.content:
            history.append(ChatTurn(question=asked, answer=message.content))
            asked = None
    return history[-turns:] if turns > 0 else []


def _source(hit: Hit, lecture_title: str | None, lecture_label: str | None) -> SourceOut:
    return SourceOut(
        lecture_id=hit.lecture_id,
        lecture_title=lecture_title,
        lecture_label=lecture_label,
        segment_id=hit.segment_id,
        start_s=hit.start_s,
        end_s=hit.end_s,
        slide_title=hit.slide_title,
        chapter=hit.chapter,
        score=hit.score,
    )


def _model_failure(error: Exception) -> str:
    if isinstance(error, ModelHTTPError) and error.status_code in (429, 503):
        return "The language model is busy right now. Try again in a minute."
    return "The answer couldn't be generated."


def _title(question: str, limit: int = 80) -> str:
    text = " ".join(question.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _sse(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _just(text: str) -> AsyncIterator[str]:
    yield text
