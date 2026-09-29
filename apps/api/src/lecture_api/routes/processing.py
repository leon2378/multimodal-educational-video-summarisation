"""Start processing and follow its progress (Phase 2b).

POST /v1/lectures/{id}/process   start the ProcessLecture workflow (idempotent)
GET  /v1/lectures/{id}/runs      processing runs, newest first
GET  /v1/lectures/{id}/events    progress as server-sent events, until the run ends
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError

from lecture_api.deps import SessionDep, TemporalDep, lecture_or_404
from lecture_api.schemas import ProgressEvent, RunOut
from lecture_core.models import LectureStatus, PipelineRun, RunStatus
from lecture_core.processing import (
    PROGRESS_QUERY,
    QUEUE_CPU,
    WORKFLOW,
    ProcessInput,
    Progress,
    StageInfo,
    workflow_id,
)

router = APIRouter(prefix="/lectures", tags=["processing"])

_PROCESSABLE = {LectureStatus.UPLOADED, LectureStatus.READY, LectureStatus.FAILED}


@router.post("/{lecture_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process(lecture_id: uuid.UUID, session: SessionDep, temporal: TemporalDep) -> RunOut:
    """Idempotent: while a run is in progress, asking again returns that run."""
    lecture = await lecture_or_404(session, lecture_id)
    if (running := await _running_run(session, lecture_id)) is not None:
        return RunOut.model_validate(running)
    if lecture.status not in _PROCESSABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lecture is {lecture.status}.")

    previous_status = lecture.status
    run = PipelineRun(lecture_id=lecture_id, workflow_id=workflow_id(lecture_id), stages=[])
    session.add(run)
    lecture.status = LectureStatus.PROCESSING
    try:
        await session.commit()
    except IntegrityError:
        # Another request started a run between our check and this insert.
        await session.rollback()
        if (running := await _running_run(session, lecture_id)) is not None:
            return RunOut.model_validate(running)
        raise

    try:
        await temporal.start_workflow(
            WORKFLOW,
            ProcessInput(lecture_id=lecture_id, run_id=run.id, source_key=lecture.source_key),
            id=run.workflow_id,
            task_queue=QUEUE_CPU,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    except RPCError as error:
        run.status, run.error = RunStatus.FAILED, f"couldn't start processing: {error}"
        lecture.status = previous_status
        await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Processing is unavailable right now."
        ) from error
    await session.refresh(run)
    return RunOut.model_validate(run)


@router.get("/{lecture_id}/runs")
async def list_runs(lecture_id: uuid.UUID, session: SessionDep) -> list[RunOut]:
    await lecture_or_404(session, lecture_id)
    runs = await session.scalars(
        select(PipelineRun)
        .where(PipelineRun.lecture_id == lecture_id)
        .order_by(PipelineRun.started_at.desc())
        .limit(20)
    )
    return [RunOut.model_validate(run) for run in runs]


@router.get(
    "/{lecture_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": ProgressEvent,
            "description": "Server-sent events. Each `data:` line is a ProgressEvent as JSON.",
        }
    },
)
async def events(
    lecture_id: uuid.UUID, request: Request, session: SessionDep, temporal: TemporalDep
) -> StreamingResponse:
    """Server-sent events: one JSON ProgressEvent per change, until the run finishes."""
    await lecture_or_404(session, lecture_id)
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker

    async def stream() -> AsyncIterator[str]:
        last = None
        while not await request.is_disconnected():
            event = await _progress_event(sessionmaker, temporal, lecture_id)
            data = event.model_dump_json()
            if data != last:
                yield f"data: {data}\n\n"
                last = data
            if event.progress is None or event.progress.status != "running":
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _running_run(session: AsyncSession, lecture_id: uuid.UUID) -> PipelineRun | None:
    running: PipelineRun | None = await session.scalar(
        select(PipelineRun).where(
            PipelineRun.lecture_id == lecture_id, PipelineRun.status == RunStatus.RUNNING
        )
    )
    return running


async def _progress_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    temporal: Client,
    lecture_id: uuid.UUID,
    ask_workflow: bool = True,
) -> ProgressEvent:
    async with sessionmaker() as session:
        lecture = await lecture_or_404(session, lecture_id)
        run = await session.scalar(
            select(PipelineRun)
            .where(PipelineRun.lecture_id == lecture_id)
            .order_by(PipelineRun.started_at.desc())
            .limit(1)
        )
    if run is None:
        return ProgressEvent(lecture_status=lecture.status, run_id=None, progress=None)
    progress = Progress(
        status=run.status.value,
        done=[StageInfo.model_validate(info) for info in run.stages],
        error=run.error,
    )
    if run.status == RunStatus.RUNNING and ask_workflow:
        # If no worker has picked the workflow up yet, report what the database knows.
        with contextlib.suppress(RPCError):
            progress = await temporal.get_workflow_handle(run.workflow_id).query(
                PROGRESS_QUERY, result_type=Progress, rpc_timeout=timedelta(seconds=2)
            )
        if progress.status != "running":
            # The run ended after the database was read. Its outcome is saved before the
            # workflow completes, so read it again rather than pair it with a stale status.
            fresh = await _progress_event(sessionmaker, temporal, lecture_id, ask_workflow=False)
            if fresh.progress is not None and fresh.progress.status != "running":
                return fresh
    return ProgressEvent(lecture_status=lecture.status, run_id=run.id, progress=progress)
