"""Request and response bodies."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lecture_core.models import LectureStatus


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
