"""Temporal activities: thin wrappers that call the stage functions (ADR 0002).

Compute activities are plain functions run on the worker's thread pool; the two that write to
Postgres are async. Every stage goes through the stage cache in object storage, so a retried
activity, or a re-run with one prompt changed, reuses everything already computed.
"""

import asyncio
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from lecture_core import metrics
from lecture_core.links import is_youtube
from lecture_core.models import (
    Lecture,
    LectureStatus,
    PipelineRun,
    RunStatus,
    SlideRow,
    SummaryRow,
    TimelineSegmentRow,
    TranscriptSegmentRow,
    UsageEvent,
    UsageKind,
)
from lecture_core.processing import ProcessInput, StageInfo
from lecture_core.storage import ObjectStorage
from lecture_core.timeline import SlideDeck, Timeline, Transcript
from lecture_llm.agents import LectureLLM, Usage
from lecture_llm.pricing import text_cost_usd
from lecture_perception import fetch, media
from lecture_perception.asr import Transcriber
from lecture_perception.media import MediaError, MediaInfo
from lecture_perception.ocr import DeckOcr, SlideOCR
from lecture_perception.slides import DetectorConfig
from lecture_pipeline import stages
from lecture_pipeline.cache import StageCache, StageResult
from lecture_pipeline.fuse import split_sentences
from lecture_pipeline.temporal.contracts import (
    AssembleInput,
    DraftInput,
    FailInput,
    FetchInput,
    IndexInput,
    IngestOutcome,
    PersistInput,
    ReadSlidesInput,
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


@dataclass(frozen=True)
class FetchSettings:
    """Lectures from a link (lecture_pipeline.settings has what each means)."""

    max_duration_s: float = 3 * 3600
    timeout_s: float = 3600
    youtube_cookies: str | None = None
    youtube_proxy: str | None = None
    # Tests only: lets a download reach this machine, where their web server is.
    allow_private: bool = False


@dataclass
class Resources:
    """What a worker needs. A worker only builds the parts its queues use: the GPU worker has
    no database, the CPU worker loads no speech model."""

    storage: ObjectStorage
    media_dir: Path
    sessionmaker: async_sessionmaker[AsyncSession] | None = None
    # Called once per activity thread (see PipelineActivities._llm).
    make_llm: Callable[[], LectureLLM] | None = None
    transcriber: Transcriber | None = None
    search: SearchResources | None = None
    # Cheap to create: the OCR models load on first use.
    ocr: SlideOCR = field(default_factory=SlideOCR)
    slide_reader: stages.SlideReaderMode = "routed"
    fetch: FetchSettings = field(default_factory=FetchSettings)


# What a fetched video is stored as, by the extension yt-dlp gave it.
_CONTENT_TYPES = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
}


class PipelineActivities:
    def __init__(self, resources: Resources) -> None:
        self.resources = resources
        self.ctx = stages.Context(StageCache(resources.storage), resources.storage)
        self._local = threading.local()

    # CPU queue

    @activity.defn(name="fetch_source")
    async def fetch_source(self, request: FetchInput) -> StageInfo:
        """Download a lecture given as a link to where an upload would be, then processing goes
        on as for an upload. Skipped when the video is there already, so processing it again
        doesn't download it again (lecture_perception.fetch)."""
        started = time.monotonic()
        storage = self.resources.storage
        if await asyncio.to_thread(storage.head, request.source_key) is not None:
            return _measured(StageInfo(stage="fetch", seconds=0.0, cached=True))
        settings = self.resources.fetch
        youtube = is_youtube(request.url)
        with tempfile.TemporaryDirectory(dir=self.resources.media_dir) as tmp:
            try:
                fetched = await fetch.download(
                    request.url,
                    Path(tmp),
                    max_bytes=request.max_bytes,
                    max_duration_s=settings.max_duration_s,
                    timeout_s=settings.timeout_s,
                    progress=lambda done: activity.heartbeat(done),
                    cookies=settings.youtube_cookies if youtube else None,
                    proxy=settings.youtube_proxy if youtube else None,
                    allow_private=settings.allow_private,
                )
                # Like an upload, it must be a lecture video before it's stored where the
                # owner can download it back.
                info = await asyncio.to_thread(media.probe, fetched.path)
                if info.duration_s > settings.max_duration_s:
                    raise fetch.FetchError(
                        f"The video is longer than {settings.max_duration_s / 3600:g} hours, "
                        "the most allowed."
                    )
            except (fetch.FetchError, MediaError) as error:
                raise ApplicationError(
                    str(error), type=type(error).__name__, non_retryable=True
                ) from error
            content_type = _CONTENT_TYPES.get(fetched.ext, "video/mp4")
            await asyncio.to_thread(
                storage.upload_file, fetched.path, request.source_key, content_type
            )
        async with self._sessionmaker()() as session, session.begin():
            lecture = await session.get(Lecture, request.lecture_id)
            if lecture is not None:
                _describe(lecture, fetched, content_type, request.title_from_source)
        return _measured(StageInfo(stage="fetch", seconds=time.monotonic() - started, cached=False))

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

    @activity.defn(name="ocr_slides")
    def ocr_slides(self, deck: StageRef) -> StageOutcome:
        ocr = self.resources.ocr
        deck_result = self._load(deck, SlideDeck)
        return _outcome("ocr", lambda: stages.ocr_slides(self.ctx, deck_result, ocr))

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
        return _measured(StageInfo(stage="index", seconds=time.monotonic() - started, cached=False))

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
            if loaded.spent_usd:
                # The quotas' ledger, which deleting the lecture leaves alone.
                owner = lecture.owner_id if lecture is not None else None
                session.add(
                    UsageEvent(user_id=owner, kind=UsageKind.LLM, cost_usd=loaded.spent_usd)
                )

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
    def read_slides(self, request: ReadSlidesInput) -> StageOutcome:
        deck = self._load(request.slides, SlideDeck)
        texts = self._load(request.ocr, DeckOcr)
        mode = self.resources.slide_reader
        return _outcome(
            "read_slides",
            lambda: stages.read_slides(self.ctx, deck, self._llm(), texts, mode),
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
        """One per activity thread. Each thread drives its own event loop for the agents' calls,
        and a client's pooled connections belong to the loop that opened them: shared between
        threads, a reused connection fails with "bound to a different event loop"."""
        if self.resources.make_llm is None:
            raise ApplicationError("this worker has no LLM configured", non_retryable=True)
        llm: LectureLLM | None = getattr(self._local, "llm", None)
        if llm is None:
            llm = self._local.llm = self.resources.make_llm()
        return llm

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
        cost = text_cost_usd(draft.model, usage.input_tokens, usage.output_tokens)
        by_stage = {
            "read_slides": readings.usage,
            "chapters": plan.usage,
            "draft_notes": draft.usage,
        }
        return _Loaded(
            info=self._load(request.probe, MediaInfo).output,
            transcript=self._load(request.transcript, Transcript).output,
            deck=self._load(request.slides, SlideDeck).output,
            readings=readings,
            timeline=self._load(request.timeline, Timeline).output,
            notes=self._load(request.notes, stages.NotesResult).output,
            # What the LLM calls cost at paid-tier prices, cached ones included.
            usage={"model": draft.model, **usage.model_dump(), "cost_usd": cost},
            spent_usd=spent_usd(draft.model, by_stage, request.stages),
        )


def spent_usd(model: str, by_stage: Mapping[str, Usage], infos: Sequence[StageInfo]) -> float:
    """What a run's own LLM calls cost at paid-tier prices: those of the LLM stages it computed,
    not the ones it took from the cache (whose usage the cache entries also carry)."""
    cached = {info.stage: info.cached for info in infos}
    spent = Usage()
    for stage, used in by_stage.items():
        if not cached.get(stage, True):
            spent = spent + used
    return text_cost_usd(model, spent.input_tokens, spent.output_tokens) or 0.0


@dataclass(frozen=True)
class _Loaded:
    info: MediaInfo
    transcript: Transcript
    deck: SlideDeck
    readings: stages.SlideReadings
    timeline: Timeline
    notes: stages.NotesResult
    usage: dict[str, object]
    # What this run's own LLM calls cost: the LLM stages it didn't take from the cache.
    spent_usd: float


def _describe(
    lecture: Lecture, fetched: fetch.Fetched, content_type: str, title_from_source: bool
) -> None:
    """What the site says about a fetched video, where the person who gave the link didn't."""
    lecture.size_bytes = fetched.size_bytes
    lecture.content_type = content_type
    lecture.source_filename = f"{(fetched.title or 'video')[:240]}.{fetched.ext}"
    if title_from_source and fetched.title:
        lecture.title = fetched.title[:300]
    if lecture.licence is None and fetched.licence:
        lecture.licence = fetched.licence[:100]
    if lecture.attribution is None and fetched.uploader:
        where = f", {fetched.webpage_url}" if fetched.webpage_url else ""
        lecture.attribution = f"{fetched.uploader}{where}"


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
                reader=reading.reader if reading else "vlm",
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
    info = StageInfo(stage=stage, seconds=time.monotonic() - started, cached=result.cached)
    return result, _measured(info)


def _measured(info: StageInfo) -> StageInfo:
    attributes = {"stage": info.stage, "cached": info.cached}
    metrics.stage_duration.record(info.seconds, attributes)
    metrics.stage_runs.add(1, attributes)
    return info


def _outcome[T: BaseModel](stage: str, step: Callable[[], StageResult[T]]) -> StageOutcome:
    result, info = _timed(stage, step)
    return StageOutcome(ref=StageRef(stage=stage, key=result.key), info=info)
