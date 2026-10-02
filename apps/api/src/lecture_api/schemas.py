"""Request and response bodies."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, computed_field

from lecture_api.quotas import QuotaUsage
from lecture_core.models import LectureStatus, MessageRole, Rating, RunStatus, Visibility
from lecture_core.notes import StudyNotes
from lecture_core.processing import Progress, StageInfo
from lecture_llm.pricing import text_cost_usd
from lecture_rag.search import SearchMode


class LectureCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=255, examples=["6.006-lecture-01.mp4"])
    # The real check (ffprobe: codec, duration, size) happens at ingest.
    content_type: str = Field(pattern=r"^video/[\w.+-]+$", examples=["video/mp4"])
    licence: str | None = Field(default=None, max_length=100, examples=["CC BY-NC-SA 4.0"])
    attribution: str | None = Field(default=None, max_length=2000)
    course_id: uuid.UUID | None = None
    # The file's size, when known: a file over the limit is refused before it's uploaded.
    size_bytes: int | None = Field(default=None, ge=1, examples=[734003200])


class LectureFromUrl(BaseModel):
    url: str = Field(
        min_length=1, max_length=2048, examples=["https://www.youtube.com/watch?v=..."]
    )
    # Without one, the video's own title, once it's downloaded.
    title: str | None = Field(default=None, min_length=1, max_length=300)
    # Without these, what the site says, if it says (YouTube gives a video's licence).
    licence: str | None = Field(default=None, max_length=100, examples=["CC BY-NC-SA 4.0"])
    attribution: str | None = Field(default=None, max_length=2000)
    course_id: uuid.UUID | None = None


class LectureUpdate(BaseModel):
    """Only the fields sent are changed. `course_id: null` takes a lecture out of its course.
    Only admins change `visibility`."""

    course_id: uuid.UUID | None = None
    visibility: Visibility | None = None


class LectureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    course_id: uuid.UUID | None
    status: LectureStatus
    source_filename: str
    # The link it was downloaded from, for a lecture given as one.
    source_url: str | None
    content_type: str
    # Before the upload's finished, the size its uploader gave.
    size_bytes: int | None
    duration_s: float | None
    # SHA-256 of the video, set by processing.
    content_hash: str | None
    licence: str | None
    attribution: str | None
    # `public`: anyone can read it; `private`: its owner and admins. No owner: added without
    # sign-in (the demo lectures).
    visibility: Visibility
    owner_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300, pattern=r"\S")
    description: str | None = Field(default=None, max_length=2000)
    # Only admins make a public course; otherwise it's private to whoever made it.
    visibility: Visibility | None = None


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    visibility: Visibility
    owner_id: uuid.UUID | None
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
    # One PUT of the whole file. To upload in parts instead, which can be resumed, ask
    # upload-parts for the parts' URLs.
    upload: UploadTarget


class UploadPartsRequest(BaseModel):
    # The whole file's size. Resuming, it must be the size the upload started with.
    size_bytes: int = Field(ge=1, examples=[734003200])


class PartTarget(BaseModel):
    number: int
    url: str


class PartsUpload(BaseModel):
    """An upload in parts. Part n is the file's bytes from (n - 1) * part_bytes up to
    n * part_bytes. PUT each one in `parts` to its URL, with no other headers, in any order
    and several at a time, then POST complete-upload. Parts storage has already are left out.
    The URLs expire: ask again for new ones."""

    size_bytes: int
    part_bytes: int
    count: int
    # The numbers of the parts storage has.
    uploaded: list[int]
    # The parts still to send.
    parts: list[PartTarget]
    expires_in_s: int


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


class SlideTextPart(BaseModel):
    """Part of a slide's text: plain text, or a formula written out in it (`math`), as LaTeX."""

    value: str
    math: bool


class SlideOut(BaseModel):
    slide_id: int
    image_url: str
    first_seen_s: float
    title: str
    text: str
    figure_description: str
    latex: list[str]
    # The text with the formulas it writes out in their places, and the formulas it doesn't:
    # the two show a slide's text and formulas as one, with nothing twice.
    text_parts: list[SlideTextPart]
    latex_not_in_text: list[str]
    code: str
    # Who read the slide: the vision LLM, or OCR alone for slides with nothing but text.
    reader: Literal["vlm", "ocr"]
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


class UsageOut(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


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
    # Tokens the answer took, the rewrite of a follow-up included.
    usage: UsageOut | None = None
    first_token_ms: int | None = None
    total_ms: int | None = None
    error: str | None = None
    feedback: FeedbackOut | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_usd(self) -> float | None:
        """What the answer's tokens cost at paid-tier prices; None without a known price."""
        if self.usage is None or self.model is None:
            return None
        return text_cost_usd(self.model, self.usage.input_tokens, self.usage.output_tokens)


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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    name: str | None


class MeOut(BaseModel):
    """Who the API takes the caller to be, and what they may still do today."""

    # Whether the API checks sign-in. Without it, every caller is one local user with no limits.
    auth: bool
    signed_in: bool
    user: UserOut | None
    admin: bool
    # A signed-in user's quotas; none for admins, the local user and anonymous visitors.
    quotas: QuotaUsage | None
