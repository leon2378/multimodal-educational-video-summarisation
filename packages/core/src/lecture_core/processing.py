"""What the API and the workers agree on: the workflow, its task queues, input and progress.

Kept in core so the API can start and query workflows by name without importing the pipeline
and its heavy dependencies (PyAV, faster-whisper, Pydantic AI).
"""

import uuid
from typing import Literal

from pydantic import BaseModel

WORKFLOW = "ProcessLecture"
PROGRESS_QUERY = "progress"

# Workflow tasks and CPU activities share a queue. GPU work runs one at a time on a 6 GB card,
# and LLM calls get their own queue so rate limits never block media work.
QUEUE_CPU = "cpu"
QUEUE_GPU = "gpu"
QUEUE_LLM = "llm"


def workflow_id(lecture_id: uuid.UUID) -> str:
    """One workflow id per lecture, so starting it twice attaches to the running one."""
    return f"process-{lecture_id}"


class ProcessInput(BaseModel):
    lecture_id: uuid.UUID
    run_id: uuid.UUID
    source_key: str


class StageInfo(BaseModel):
    stage: str
    seconds: float
    cached: bool


class Progress(BaseModel):
    status: Literal["running", "succeeded", "failed"] = "running"
    running: list[str] = []
    done: list[StageInfo] = []
    error: str | None = None
