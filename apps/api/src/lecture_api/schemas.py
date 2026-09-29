"""Request and response bodies."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from lecture_core.models import LectureStatus, MessageRole, Rating, RunStatus
from lecture_core.notes import StudyNotes
from lecture_core.processing import Progress, StageInfo
from lecture_rag.search import SearchMode


class LectureCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=255, examples=["6.006-lecture-01.mp4"])
    # The real check (ffprobe: codec, duration, size) happens at ingest.
    content_type: str = Field(pattern=r"^video/[\w.+-]+$", examples=["video/mp4"])
    licence: str | None = Field(default=None, max_length=100, examples=["CC BY-NC-SA 4.0"])
    attribution: str | None = Field(default=None, max_length=2000)
    course_id: uuid.UUID | None = None


class LectureUpdate(BaseModel):
    """Only the fields sent are changed. `course_id: null` takes a lecture out of its course."""

    course_id: uuid.UUID | None = None


class LectureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    course_id: uuid.UUID | None
    status: LectureStatus
    source_filename: str
    content_type: str
    size_bytes: int | None
    duration_s: float | None
    # SHA-256 of the video, set by processing.
    content_hash: str | None
    licence: str | None
    attribution: str | None
    created_at: datetime
    updated_at: datetime


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    description: str | None = Field(default=None, max_length=2000)


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    lecture_count: int = 0
    created_at: datetime
    updated_at: datetime


class CourseDetail(CourseOut):
    lectures: list[LectureOut]


class UploadTarget(BaseModel):
    """Send the file with `method` to `url`, including `headers` exactly as given."""

    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_in_s: int


class LectureCreated(BaseModel):
    lecture: LectureOut
    upload: UploadTarget


class MediaOut(BaseModel):
    """Where the browser plays the lecture from: a presigned URL straight to storage."""

    url: str
    content_type: str
    expires_in_s: int


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lecture_id: uuid.UUID
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    stages: list[StageInfo]
    llm_usage: dict[str, Any] | None


class ProgressEvent(BaseModel):
    """One server-sent event: the lecture's status and its latest run's progress."""

    lecture_status: LectureStatus
    run_id: uuid.UUID | None
    progress: Progress | None


class TranscriptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    start_s: float
    end_s: float
    text: str


class TimeSpan(BaseModel):
    start_s: float
    end_s: float


class SlideOut(BaseModel):
    slide_id: int
    image_url: str
    first_seen_s: float
    title: str
    text: str
    figure_description: str
    latex: list[str]
    code: str
    # Every stretch of the video during which this is the current slide.
    spans: list[TimeSpan]


class TimelineSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    segment_id: str
    start_s: float
    end_s: float
    transcript: str
    slide_id: int | None


class NotesOut(BaseModel):
    notes: StudyNotes
    model: str
    run_id: uuid.UUID | None
    created_at: datetime


class SearchHitOut(BaseModel):
    lecture_id: uuid.UUID
    segment_id: str
    start_s: float
    end_s: float
    slide_id: int | None
    slide_title: str | None
    chapter: str | None
    transcript: str
    # The slide's title and text, then the transcript: what the search matched.
    text: str
    # Only comparable between hits of one search.
    score: float


class SearchResults(BaseModel):
    query: str
    mode: SearchMode
    hits: list[SearchHitOut]


# Q&A


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    # Continue a conversation; leave out to start a new one.
    thread_id: uuid.UUID | None = None


class SourceOut(BaseModel):
    """A retrieved segment the answer drew on."""

    lecture_id: uuid.UUID
    lecture_title: str | None = None
    # In an answer across a course, how its citations name this lecture, e.g. "L2".
    lecture_label: str | None = None
    segment_id: str
    start_s: float
    end_s: float
    slide_title: str | None
    chapter: str | None
    score: float


class CitationOut(BaseModel):
    # As written in the answer, e.g. "[12:34]", or "[L2 12:34]" in an answer across a course.
    label: str
    at_s: float
    lecture_id: uuid.UUID | None = None
    segment_id: str | None
    # False when it points outside every retrieved segment.
    valid: bool


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: Rating
    reason: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: uuid.UUID
    rating: Rating
    reason: str | None
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    # Answers only.
    search_query: str | None = None
    sources: list[SourceOut] | None = None
    citations: list[CitationOut] | None = None
    model: str | None = None
    first_token_ms: int | None = None
    total_ms: int | None = None
    error: str | None = None
    feedback: FeedbackOut | None = None


class ThreadOut(BaseModel):
    """A conversation about a lecture or, with `course_id` set instead, a whole course."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lecture_id: uuid.UUID | None
    course_id: uuid.UUID | None
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


# What POST /lectures/{id}/ask streams, in order: start, sources, any number of deltas, then
# done or error.


class AskStart(BaseModel):
    type: Literal["start"] = "start"
    thread_id: uuid.UUID
    question: MessageOut


class AskSources(BaseModel):
    type: Literal["sources"] = "sources"
    # The question as searched: a follow-up is first rewritten to stand on its own.
    search_query: str
    sources: list[SourceOut]


class AskDelta(BaseModel):
    type: Literal["delta"] = "delta"
    text: str


class AskDone(BaseModel):
    type: Literal["done"] = "done"
    answer: MessageOut


class AskError(BaseModel):
    type: Literal["error"] = "error"
    detail: str
    # The failed answer as stored, when it got that far.
    answer: MessageOut | None


class AskEvent(
    RootModel[
        Annotated[
            AskStart | AskSources | AskDelta | AskDone | AskError, Field(discriminator="type")
        ]
    ]
):
    pass
