"""Activity inputs and outputs. Light on purpose: the workflow sandbox imports this module.

Activities hand each other stage-cache refs, not data. A 51-minute transcript with word
timings can pass Temporal's 2 MB payload limit, and the cache already holds every result.
"""

import uuid

from pydantic import BaseModel

from lecture_core.processing import StageInfo


class StageRef(BaseModel):
    stage: str
    key: str


class StageOutcome(BaseModel):
    ref: StageRef
    info: StageInfo


class IngestOutcome(BaseModel):
    video_sha256: str
    probe: StageRef
    audio: StageRef
    info: list[StageInfo]


class SlidesInput(BaseModel):
    source_key: str
    video_sha256: str
    probe: StageRef


class TimelineInput(BaseModel):
    transcript: StageRef
    slides: StageRef
    readings: StageRef


class DraftInput(BaseModel):
    timeline: StageRef
    chapters: StageRef


class AssembleInput(BaseModel):
    timeline: StageRef
    draft: StageRef


class PersistInput(BaseModel):
    lecture_id: uuid.UUID
    run_id: uuid.UUID
    video_sha256: str
    probe: StageRef
    transcript: StageRef
    slides: StageRef
    readings: StageRef
    timeline: StageRef
    chapters: StageRef
    draft: StageRef
    notes: StageRef
    stages: list[StageInfo]


class FailInput(BaseModel):
    lecture_id: uuid.UUID
    run_id: uuid.UUID
    error: str
    stages: list[StageInfo]
