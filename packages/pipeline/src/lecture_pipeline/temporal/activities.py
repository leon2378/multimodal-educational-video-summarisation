"""Temporal activities: thin wrappers that call the stage functions (ADR 0002).

Compute activities are plain functions run on the worker's thread pool; the two that write to
Postgres are async. Every stage goes through the stage cache in object storage, so a retried
activity, or a re-run with one prompt changed, reuses everything already computed.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from lecture_core.models import (
    Lecture,
    LectureStatus,
    PipelineRun,
    RunStatus,
    SlideRow,
    SummaryRow,
    TimelineSegmentRow,
    TranscriptSegmentRow,
)
from lecture_core.processing import ProcessInput, StageInfo
from lecture_core.storage import ObjectStorage
from lecture_core.timeline import SlideDeck, Timeline, Transcript
from lecture_llm.agents import LectureLLM
from lecture_perception.asr import Transcriber
from lecture_perception.media import MediaError, MediaInfo
from lecture_perception.slides import DetectorConfig
from lecture_pipeline import stages
from lecture_pipeline.cache import StageCache, StageResult
from lecture_pipeline.fuse import split_sentences
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
from lecture_rag.encoders import ChunkEmbeddings, DenseEncoder, SparseEncoder
from lecture_rag.index import SearchIndex


@dataclass
class SearchResources:
    index: SearchIndex
    dense: DenseEncoder
    sparse: SparseEncoder


@dataclass
class Resources:
    """What a worker needs. A worker only builds the parts its queues use: the GPU worker has
    no database, the CPU worker loads no speech model."""

    storage: ObjectStorage
    media_dir: Path
    sessionmaker: async_sessionmaker[AsyncSession] | None = None
    llm: LectureLLM | None = None
    transcriber: Transcriber | None = None
    search: SearchResources | None = None


class PipelineActivities:
    def __init__(self, resources: Resources) -> None:
        self.resources = resources
        self.ctx = stages.Context(StageCache(resources.storage), resources.storage)

    # CPU queue

    @activity.defn(name="ingest")
    def ingest(self, request: ProcessInput) -> IngestOutcome:
        video, sha = self._video(request.source_key, expected_sha256=None)
        probe, probe_info = _timed("probe", lambda: stages.probe(self.ctx, video, sha))
        audio, audio_info = _timed("audio", lambda: stages.audio(self.ctx, video, sha))
        return IngestOutcome(
            video_sha256=sha,
            probe=StageRef(stage="probe", key=probe.key),
            audio=StageRef(stage="audio", key=audio.key),
            info=[probe_info, audio_info],
        )

    @activity.defn(name="detect_slides")
    def detect_slides(self, request: SlidesInput) -> StageOutcome:
        video, sha = self._video(request.source_key, request.video_sha256)
        info = self._load(request.probe, MediaInfo)
        return _outcome(
            "slides",
            lambda: stages.slides(self.ctx, video, sha, info.output, DetectorConfig()),
        )

    @activity.defn(name="build_timeline")
    def build_timeline(self, request: TimelineInput) -> StageOutcome:
        transcript = self._load(request.transcript, Transcript)
        deck = self._load(request.slides, SlideDeck)
        readings = self._load(request.readings, stages.SlideReadings)
        return _outcome("timeline", lambda: stages.timeline(self.ctx, transcript, deck, readings))

    @activity.defn(name="assemble_notes")
    def assemble_notes(self, request: AssembleInput) -> StageOutcome:
        timeline = self._load(request.timeline, Timeline)
        draft = self._load(request.draft, stages.NotesDraft)
        return _outcome("notes", lambda: stages.notes(self.ctx, timeline, draft))

    @activity.defn(name="embed_segments")
    def embed_segments(self, timeline: StageRef) -> StageOutcome:
        search = self._search()
        timeline_result = self._load(timeline, Timeline)
        return _outcome(
            "embed", lambda: stages.embed(self.ctx, timeline_result, search.dense, search.sparse)
        )

    @activity.defn(name="index_lecture")
    def index_lecture(self, request: IndexInput) -> StageInfo:
        """Replace the lecture's points in the search index. Not cached: it writes to Qdrant,
        and it's quick."""
        started = time.monotonic()
        embeddings = self._load(request.embeddings, ChunkEmbeddings).output
        notes = self._load(request.notes, stages.NotesResult).output.notes
        self._search().index.replace_lecture(request.lecture_id, embeddings.chunks, notes.chapters)
        return StageInfo(stage="index", seconds=time.monotonic() - started, cached=False)

    @activity.defn(name="persist_results")
    async def persist_results(self, request: PersistInput) -> None:
        """Replace the lecture's results in one transaction and mark it ready."""
        loaded = await asyncio.to_thread(self._load_for_persist, request)
        async with self._sessionmaker()() as session, session.begin():
            lecture_id = request.lecture_id
            for table in (TranscriptSegmentRow, SlideRow, TimelineSegmentRow, SummaryRow):
                await session.execute(delete(table).where(table.lecture_id == lecture_id))
            session.add_all(_result_rows(request, loaded))

            lecture = await session.get(Lecture, lecture_id)
            if lecture is not None:
                lecture.status = LectureStatus.READY
                lecture.content_hash = request.video_sha256
                lecture.duration_s = loaded.info.duration_s
            run = await session.get(PipelineRun, request.run_id)
            if run is not None:
                run.status = RunStatus.SUCCEEDED
                run.finished_at = datetime.now(UTC)
                run.stages = [info.model_dump() for info in request.stages]
                run.llm_usage = loaded.usage

    @activity.defn(name="mark_failed")
    async def mark_failed(self, request: FailInput) -> None:
        async with self._sessionmaker()() as session, session.begin():
            run = await session.get(PipelineRun, request.run_id)
            if run is not None:
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error = request.error
                run.stages = [info.model_dump() for info in request.stages]
            lecture = await session.get(Lecture, request.lecture_id)
            if lecture is not None:
                lecture.status = LectureStatus.FAILED

    # GPU queue

    @activity.defn(name="transcribe")
    def transcribe(self, audio: StageRef) -> StageOutcome:
        transcriber = self.resources.transcriber
        if transcriber is None:
            raise ApplicationError("this worker has no speech model", non_retryable=True)
        audio_result = self._load(audio, stages.AudioArtifact)
        # Heartbeats let Temporal tell a long transcription from a stuck one.
        return _outcome(
            "asr",
            lambda: stages.transcribe(self.ctx, audio_result, transcriber, activity.heartbeat),
        )

    # LLM queue

    @activity.defn(name="read_slides")
    def read_slides(self, deck: StageRef) -> StageOutcome:
        deck_result = self._load(deck, SlideDeck)
        return _outcome(
            "read_slides", lambda: stages.read_slides(self.ctx, deck_result, self._llm())
        )

    @activity.defn(name="plan_chapters")
    def plan_chapters(self, timeline: StageRef) -> StageOutcome:
        timeline_result = self._load(timeline, Timeline)
        return _outcome("chapters", lambda: stages.chapters(self.ctx, timeline_result, self._llm()))

    @activity.defn(name="draft_notes")
    def draft_notes(self, request: DraftInput) -> StageOutcome:
        timeline = self._load(request.timeline, Timeline)
        plan = self._load(request.chapters, stages.ChapterPlan)
        return _outcome(
            "draft_notes", lambda: stages.draft_notes(self.ctx, timeline, plan, self._llm())
        )

    # Helpers

    def _video(self, source_key: str, expected_sha256: str | None) -> tuple[Path, str]:
        """The source video on local disk, downloaded once per worker and named by hash."""
        suffix = Path(source_key).suffix
        media_dir = self.resources.media_dir
        if expected_sha256 is not None:
            cached = media_dir / f"{expected_sha256}{suffix}"
            if cached.is_file():
                return cached, expected_sha256
        download = media_dir / f"download-{uuid.uuid4().hex}{suffix}"
        self.resources.storage.download_file(source_key, download)
        sha = stages.file_sha256(download)
        if expected_sha256 is not None and sha != expected_sha256:
            download.unlink()
            raise ApplicationError(f"{source_key} changed during processing", non_retryable=True)
        target = media_dir / f"{sha}{suffix}"
        download.replace(target)
        return target, sha

    def _load[T: BaseModel](self, ref: StageRef, output_type: type[T]) -> StageResult[T]:
        return self.ctx.cache.load(ref.stage, ref.key, output_type)

    def _llm(self) -> LectureLLM:
        if self.resources.llm is None:
            raise ApplicationError("this worker has no LLM configured", non_retryable=True)
        return self.resources.llm

    def _search(self) -> SearchResources:
        if self.resources.search is None:
            raise ApplicationError("this worker has no search index", non_retryable=True)
        return self.resources.search

    def _sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self.resources.sessionmaker is None:
            raise ApplicationError("this worker has no database", non_retryable=True)
        return self.resources.sessionmaker

    def _load_for_persist(self, request: PersistInput) -> "_Loaded":
        readings = self._load(request.readings, stages.SlideReadings).output
        plan = self._load(request.chapters, stages.ChapterPlan).output
        draft = self._load(request.draft, stages.NotesDraft).output
        usage = readings.usage + plan.usage + draft.usage
        return _Loaded(
            info=self._load(request.probe, MediaInfo).output,
            transcript=self._load(request.transcript, Transcript).output,
            deck=self._load(request.slides, SlideDeck).output,
            readings=readings,
            timeline=self._load(request.timeline, Timeline).output,
            notes=self._load(request.notes, stages.NotesResult).output,
            usage={"model": draft.model, **usage.model_dump()},
        )


@dataclass(frozen=True)
class _Loaded:
    info: MediaInfo
    transcript: Transcript
    deck: SlideDeck
    readings: stages.SlideReadings
    timeline: Timeline
    notes: stages.NotesResult
    usage: dict[str, object]


def _result_rows(
    request: PersistInput, loaded: _Loaded
) -> list[TranscriptSegmentRow | SlideRow | TimelineSegmentRow | SummaryRow]:
    lecture_id = request.lecture_id
    readings = {r.slide_id: r for r in loaded.readings.readings}
    rows: list[TranscriptSegmentRow | SlideRow | TimelineSegmentRow | SummaryRow] = []
    # Sentence by sentence, so the web page's transcript can follow playback line by line.
    sentences = [
        line for segment in loaded.transcript.segments for line in split_sentences(segment)
    ]
    rows += [
        TranscriptSegmentRow(
            lecture_id=lecture_id,
            index=i,
            start_s=sentence.start_s,
            end_s=sentence.end_s,
            text=sentence.text,
            words=[word.model_dump() for word in sentence.words],
        )
        for i, sentence in enumerate(sentences)
    ]
    for slide in loaded.deck.slides:
        reading = readings.get(slide.id)
        rows.append(
            SlideRow(
                lecture_id=lecture_id,
                slide_id=slide.id,
                image_key=slide.image_key,
                first_seen_s=slide.first_seen_s,
                title=reading.title if reading else "",
                text=reading.text if reading else "",
                figure_description=reading.figure_description if reading else "",
                latex=reading.latex if reading else [],
                code=reading.code if reading else "",
                spans=[
                    {"start_s": span.start_s, "end_s": span.end_s}
                    for span in loaded.deck.spans
                    if span.slide_id == slide.id
                ],
            )
        )
    rows += [
        TimelineSegmentRow(
            lecture_id=lecture_id,
            segment_id=segment.id,
            index=i,
            start_s=segment.start_s,
            end_s=segment.end_s,
            transcript=segment.transcript,
            slide_id=segment.slide_id,
        )
        for i, segment in enumerate(loaded.timeline.segments)
    ]
    rows.append(
        SummaryRow(
            lecture_id=lecture_id,
            kind="study_notes",
            run_id=request.run_id,
            content=loaded.notes.notes.model_dump(mode="json"),
            model=str(loaded.usage.get("model", "unknown")),
        )
    )
    return rows


def _timed[T: BaseModel](
    stage: str, step: Callable[[], StageResult[T]]
) -> tuple[StageResult[T], StageInfo]:
    started = time.monotonic()
    try:
        result = step()
    except MediaError as error:
        raise ApplicationError(str(error), type="MediaError", non_retryable=True) from error
    return result, StageInfo(stage=stage, seconds=time.monotonic() - started, cached=result.cached)


def _outcome[T: BaseModel](stage: str, step: Callable[[], StageResult[T]]) -> StageOutcome:
    result, info = _timed(stage, step)
    return StageOutcome(ref=StageRef(stage=stage, key=result.key), info=info)
