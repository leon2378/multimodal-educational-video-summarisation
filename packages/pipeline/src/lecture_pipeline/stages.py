"""Pipeline stages, each a plain function through the stage cache (ADR 0001).

The local CLI calls them in order. In Phase 2b, Temporal activities wrap these same functions,
passing cache keys between them rather than payloads.
"""

import hashlib
import io
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, JsonValue

from lecture_core.notes import StudyNotes
from lecture_core.timeline import SlideDeck, SlideImage, SlideReading, Timeline, Transcript
from lecture_llm.agents import ChapterNotes, LectureLLM, Overview, Usage
from lecture_llm.telemetry import record_usage
from lecture_perception import media
from lecture_perception.asr import Transcriber
from lecture_perception.ocr import DeckOcr, RoutingConfig, SlideOCR, reading_from_ocr, route
from lecture_perception.slides import DetectorConfig, detect_slides
from lecture_pipeline.assemble import ChapterRange, assemble, chapter_ranges
from lecture_pipeline.cache import ArtifactStore, StageCache, StageResult, StageSpec, files_prefix
from lecture_pipeline.fuse import build_timeline
from lecture_rag.chunks import CHUNKING_VERSION, build_chunks
from lecture_rag.encoders import ChunkEmbeddings, DenseEncoder, SparseEncoder, embed_chunks


@dataclass(frozen=True)
class Context:
    cache: StageCache
    store: ArtifactStore


class AudioArtifact(BaseModel):
    key: str
    sha256: str
    size_bytes: int


# Who reads the slides: the vision LLM (every slide), OCR (every slide), or OCR with the slides
# it can't handle routed to the vision LLM (lecture_perception.ocr).
SlideReaderMode = Literal["vlm", "routed", "ocr"]


class SlideReadings(BaseModel):
    readings: list[SlideReading]
    usage: Usage
    # Slides sent to the vision LLM when routing, with the reasons.
    routed: dict[int, list[str]] = {}


class ChapterPlan(BaseModel):
    chapters: list[ChapterRange]
    usage: Usage


class ChapterDraft(BaseModel):
    chapter: ChapterRange
    notes: ChapterNotes


class NotesDraft(BaseModel):
    """The LLM's output, before assembly. Cached apart from assembly, so changes to how notes
    are put together never re-run the LLM."""

    chapters: list[ChapterDraft]
    overview: Overview
    model: str
    usage: Usage


class NotesResult(BaseModel):
    notes: StudyNotes
    # Items the model cited to segments that don't exist.
    dropped: list[str]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def probe(ctx: Context, video: Path, video_sha256: str) -> StageResult[media.MediaInfo]:
    return ctx.cache.run(
        StageSpec("probe", "1"),
        {"video": video_sha256},
        media.MediaInfo,
        lambda _key: media.probe(video),
    )


def audio(ctx: Context, video: Path, video_sha256: str) -> StageResult[AudioArtifact]:
    sample_rate = 16_000

    def compute(key: str) -> AudioArtifact:
        data = media.extract_audio(video, sample_rate)
        audio_key = f"{files_prefix('audio', key)}/audio.flac"
        ctx.store.put_bytes(audio_key, data, "audio/flac")
        return AudioArtifact(
            key=audio_key, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)
        )

    spec = StageSpec("audio", "1", params={"sample_rate": sample_rate, "format": "flac"})
    return ctx.cache.run(spec, {"video": video_sha256}, AudioArtifact, compute)


def transcribe(
    ctx: Context,
    audio: StageResult[AudioArtifact],
    transcriber: Transcriber,
    on_progress: Callable[[float], None] | None = None,
) -> StageResult[Transcript]:
    def compute(_key: str) -> Transcript:
        audio_bytes = _require(ctx.store, audio.output.key)
        return transcriber.transcribe(io.BytesIO(audio_bytes), on_progress)

    spec = StageSpec("asr", "1", model=transcriber.model_id, params=transcriber.cache_params())
    return ctx.cache.run(spec, {"audio": audio.key}, Transcript, compute)


def slides(
    ctx: Context,
    video: Path,
    video_sha256: str,
    info: media.MediaInfo,
    config: DetectorConfig,
    fps: float = 1.0,
) -> StageResult[SlideDeck]:
    def compute(key: str) -> SlideDeck:
        detection = detect_slides(media.sample_frames(video, fps), info.duration_s, config)
        images = []
        for slide in detection.slides:
            buffer = io.BytesIO()
            slide.image.save(buffer, "JPEG", quality=90)
            image_key = f"{files_prefix('slides', key)}/{slide.id:03d}.jpg"
            ctx.store.put_bytes(image_key, buffer.getvalue(), "image/jpeg")
            images.append(
                SlideImage(id=slide.id, image_key=image_key, first_seen_s=slide.first_seen_s)
            )
        return SlideDeck(slides=images, spans=detection.spans, slide_share=detection.slide_share)

    spec = StageSpec("slides", "1", params={"fps": fps, **asdict(config)})
    return ctx.cache.run(spec, {"video": video_sha256}, SlideDeck, compute)


def ocr_slides(ctx: Context, deck: StageResult[SlideDeck], ocr: SlideOCR) -> StageResult[DeckOcr]:
    def compute(_key: str) -> DeckOcr:
        texts = []
        for slide in deck.output.slides:
            image = Image.open(io.BytesIO(_require(ctx.store, slide.image_key)))
            texts.append(ocr.read(slide.id, image))
        return DeckOcr(slides=texts, model=ocr.model_id)

    spec = StageSpec("ocr", "1", model=ocr.model_id)
    return ctx.cache.run(spec, {"slides": deck.key}, DeckOcr, compute)


def read_slides(
    ctx: Context,
    deck: StageResult[SlideDeck],
    llm: LectureLLM,
    texts: StageResult[DeckOcr] | None = None,
    mode: SlideReaderMode = "vlm",
    routing: RoutingConfig | None = None,
) -> StageResult[SlideReadings]:
    """Slide titles, text, figures, LaTeX and code. `vlm` keeps the cache key it always had, so
    lectures read that way before OCR existed aren't read again."""
    routing = routing or RoutingConfig()
    vlm_params: dict[str, JsonValue] = {
        "prompt": llm.prompts.read_slides.fingerprint,
        "per_request": llm.slides_per_request,
    }

    def with_vlm(slide_ids: set[int]) -> tuple[dict[int, SlideReading], Usage]:
        images = [(s.id, _require(ctx.store, s.image_key)) for s in deck.output.slides]
        readings, usage = llm.read_slides([image for image in images if image[0] in slide_ids])
        record_usage(usage, llm.model_name, "read_slides")
        return {reading.slide_id: reading for reading in readings}, usage

    if mode == "vlm":
        spec = StageSpec("read_slides", "1", model=llm.model_name, params=vlm_params)

        def compute_vlm(_key: str) -> SlideReadings:
            readings, usage = with_vlm({s.id for s in deck.output.slides})
            return SlideReadings(readings=[readings[s.id] for s in deck.output.slides], usage=usage)

        return ctx.cache.run(spec, {"slides": deck.key}, SlideReadings, compute_vlm)

    if texts is None:
        raise ValueError(f"slide reader {mode!r} needs the OCR stage's output")
    ocr_texts = texts.output

    def compute(_key: str) -> SlideReadings:
        routed = route(ocr_texts, routing) if mode == "routed" else {}
        readings = {s.slide_id: reading_from_ocr(s) for s in ocr_texts.slides}
        usage = Usage()
        if routed:
            from_vlm, usage = with_vlm(set(routed))
            readings |= from_vlm
        return SlideReadings(
            readings=[readings[s.id] for s in deck.output.slides], usage=usage, routed=routed
        )

    params: dict[str, JsonValue] = {"mode": mode}
    if mode == "routed":
        params |= {**vlm_params, **asdict(routing)}
    spec = StageSpec(
        "read_slides", "2", model=llm.model_name if mode == "routed" else None, params=params
    )
    return ctx.cache.run(spec, {"slides": deck.key, "ocr": texts.key}, SlideReadings, compute)


def timeline(
    ctx: Context,
    transcript: StageResult[Transcript],
    deck: StageResult[SlideDeck],
    readings: StageResult[SlideReadings],
    max_segment_s: float = 90.0,
) -> StageResult[Timeline]:
    spec = StageSpec("timeline", "1", params={"max_segment_s": max_segment_s})
    inputs = {"transcript": transcript.key, "slides": deck.key, "readings": readings.key}
    return ctx.cache.run(
        spec,
        inputs,
        Timeline,
        lambda _key: build_timeline(
            transcript.output, deck.output, readings.output.readings, max_segment_s
        ),
    )


def chapters(
    ctx: Context, timeline: StageResult[Timeline], llm: LectureLLM
) -> StageResult[ChapterPlan]:
    def compute(_key: str) -> ChapterPlan:
        planned, usage = llm.plan_chapters(timeline.output)
        record_usage(usage, llm.model_name, "chapters")
        return ChapterPlan(chapters=chapter_ranges(timeline.output, planned), usage=usage)

    spec = StageSpec(
        "chapters", "1", model=llm.model_name, params={"prompt": llm.prompts.chapters.fingerprint}
    )
    return ctx.cache.run(spec, {"timeline": timeline.key}, ChapterPlan, compute)


def draft_notes(
    ctx: Context,
    timeline: StageResult[Timeline],
    plan: StageResult[ChapterPlan],
    llm: LectureLLM,
) -> StageResult[NotesDraft]:
    """Map (notes per chapter), then reduce (TL;DR and quiz across the lecture)."""

    def compute(_key: str) -> NotesDraft:
        usage = Usage()
        drafts = []
        for chapter in plan.output.chapters:
            segments = timeline.output.segments[chapter.first : chapter.last + 1]
            chapter_notes, used = llm.chapter_notes(timeline.output, segments)
            usage = usage + used
            drafts.append(ChapterDraft(chapter=chapter, notes=chapter_notes))
        overview, used = llm.overview(timeline.output, [(d.chapter.title, d.notes) for d in drafts])
        record_usage(usage + used, llm.model_name, "draft_notes")
        return NotesDraft(
            chapters=drafts, overview=overview, model=llm.model_name, usage=usage + used
        )

    # Version 2: the output records the model.
    spec = StageSpec(
        "draft_notes",
        "2",
        model=llm.model_name,
        params={
            "chapter_prompt": llm.prompts.chapter_notes.fingerprint,
            "overview_prompt": llm.prompts.overview.fingerprint,
        },
    )
    return ctx.cache.run(
        spec, {"timeline": timeline.key, "chapters": plan.key}, NotesDraft, compute
    )


def notes(
    ctx: Context, timeline: StageResult[Timeline], draft: StageResult[NotesDraft]
) -> StageResult[NotesResult]:
    """Assembly: segment ids to times, word-level concept times, duplicate concepts merged."""

    def compute(_key: str) -> NotesResult:
        study_notes, dropped = assemble(
            timeline.output,
            [(d.chapter, d.notes) for d in draft.output.chapters],
            draft.output.overview,
        )
        return NotesResult(notes=study_notes, dropped=dropped)

    # Bump the version when assembly logic changes; the LLM draft stays cached.
    return ctx.cache.run(
        StageSpec("notes", "2"),
        {"timeline": timeline.key, "draft": draft.key},
        NotesResult,
        compute,
    )


def embed(
    ctx: Context, timeline: StageResult[Timeline], dense: DenseEncoder, sparse: SparseEncoder
) -> StageResult[ChunkEmbeddings]:
    """Dense and BM25 vectors for each segment's chunk, ready to index for search."""
    spec = StageSpec(
        "embed",
        "1",
        model=dense.model_id,
        params={"sparse": sparse.model_id, "chunking": CHUNKING_VERSION},
    )
    return ctx.cache.run(
        spec,
        {"timeline": timeline.key},
        ChunkEmbeddings,
        lambda _key: embed_chunks(build_chunks(timeline.output), dense, sparse),
    )


def _require(store: ArtifactStore, key: str) -> bytes:
    data = store.get_bytes(key)
    if data is None:
        raise FileNotFoundError(f"missing artifact {key}; its cache entry exists without it")
    return data
