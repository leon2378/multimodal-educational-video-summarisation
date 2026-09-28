"""ProcessLecture: one workflow per lecture, orchestrating the stage activities.

Workflow code must be deterministic, so it only sequences activities and tracks progress; all
I/O happens in activities. Speech recognition (GPU queue) and slide detection (CPU queue) run
in parallel, and so do embedding for search and writing the notes.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from lecture_core.processing import (
        PROGRESS_QUERY,
        QUEUE_CPU,
        QUEUE_GPU,
        QUEUE_LLM,
        WORKFLOW,
        ProcessInput,
        Progress,
        StageInfo,
    )
    from lecture_pipeline.temporal.contracts import (
        AssembleInput,
        DraftInput,
        FailInput,
        IndexInput,
        IngestOutcome,
        PersistInput,
        SlidesInput,
        StageOutcome,
        StageRef,
        TimelineInput,
    )

# Bad input (not a video, no audio) fails the run at once instead of retrying.
NON_RETRYABLE = ["MediaError"]


@workflow.defn(name=WORKFLOW)
class ProcessLecture:
    def __init__(self) -> None:
        self._progress = Progress()

    @workflow.query(name=PROGRESS_QUERY)
    def progress(self) -> Progress:
        return self._progress

    @workflow.run
    async def run(self, request: ProcessInput) -> Progress:
        try:
            await self._pipeline(request)
        except ActivityError as error:
            message = str(error.cause or error)
            self._progress.status, self._progress.error = "failed", message
            self._progress.running = []
            await workflow.execute_activity(
                "mark_failed",
                FailInput(
                    lecture_id=request.lecture_id,
                    run_id=request.run_id,
                    error=message,
                    stages=self._progress.done,
                ),
                task_queue=QUEUE_CPU,
                start_to_close_timeout=timedelta(minutes=2),
            )
            raise ApplicationError(f"processing failed: {message}", non_retryable=True) from error
        self._progress.status = "succeeded"
        return self._progress

    async def _pipeline(self, request: ProcessInput) -> None:
        ingest = await self._step(
            ["probe", "audio"], "ingest", request, IngestOutcome, QUEUE_CPU, minutes=30
        )
        self._progress.done += ingest.info

        transcript, slides = await asyncio.gather(
            self._stage(
                "asr", "transcribe", ingest.audio, QUEUE_GPU, minutes=120, heartbeat_minutes=5
            ),
            self._stage(
                "slides",
                "detect_slides",
                SlidesInput(
                    source_key=request.source_key,
                    video_sha256=ingest.video_sha256,
                    probe=ingest.probe,
                ),
                QUEUE_CPU,
                minutes=30,
            ),
        )
        readings = await self._stage("read_slides", "read_slides", slides, QUEUE_LLM, minutes=30)
        timeline = await self._stage(
            "timeline",
            "build_timeline",
            TimelineInput(transcript=transcript, slides=slides, readings=readings),
            QUEUE_CPU,
            minutes=10,
        )
        embeddings, (chapters, draft, notes) = await asyncio.gather(
            self._stage("embed", "embed_segments", timeline, QUEUE_CPU, minutes=30),
            self._notes(timeline),
        )
        # Indexed before the lecture is marked ready, so a ready lecture is searchable.
        self._progress.done.append(
            await self._step(
                ["index"],
                "index_lecture",
                IndexInput(lecture_id=request.lecture_id, embeddings=embeddings, notes=notes),
                StageInfo,
                QUEUE_CPU,
                minutes=10,
            )
        )
        await self._step(
            ["save"],
            "persist_results",
            PersistInput(
                lecture_id=request.lecture_id,
                run_id=request.run_id,
                video_sha256=ingest.video_sha256,
                probe=ingest.probe,
                transcript=transcript,
                slides=slides,
                readings=readings,
                timeline=timeline,
                chapters=chapters,
                draft=draft,
                notes=notes,
                stages=self._progress.done,
            ),
            type(None),
            QUEUE_CPU,
            minutes=5,
        )

    async def _notes(self, timeline: StageRef) -> tuple[StageRef, StageRef, StageRef]:
        chapters = await self._stage("chapters", "plan_chapters", timeline, QUEUE_LLM, minutes=15)
        draft = await self._stage(
            "draft_notes",
            "draft_notes",
            DraftInput(timeline=timeline, chapters=chapters),
            QUEUE_LLM,
            minutes=30,
        )
        notes = await self._stage(
            "notes",
            "assemble_notes",
            AssembleInput(timeline=timeline, draft=draft),
            QUEUE_CPU,
            minutes=5,
        )
        return chapters, draft, notes

    async def _stage(
        self,
        label: str,
        activity: str,
        arg: Any,
        queue: str,
        minutes: int,
        heartbeat_minutes: int | None = None,
    ) -> StageRef:
        outcome = await self._step(
            [label], activity, arg, StageOutcome, queue, minutes, heartbeat_minutes
        )
        self._progress.done.append(outcome.info)
        return outcome.ref

    async def _step[T](
        self,
        labels: list[str],
        activity: str,
        arg: Any,
        result_type: type[T],
        queue: str,
        minutes: int,
        heartbeat_minutes: int | None = None,
    ) -> T:
        self._progress.running += labels
        try:
            result: T = await workflow.execute_activity(
                activity,
                arg,
                result_type=result_type,
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=minutes),
                heartbeat_timeout=(
                    timedelta(minutes=heartbeat_minutes) if heartbeat_minutes else None
                ),
                retry_policy=RetryPolicy(
                    maximum_attempts=3, non_retryable_error_types=NON_RETRYABLE
                ),
            )
        finally:
            self._progress.running = [r for r in self._progress.running if r not in labels]
        return result
