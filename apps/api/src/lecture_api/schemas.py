"""Request and response bodies."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lecture_core.models import LectureStatus, RunStatus
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


class LectureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
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
