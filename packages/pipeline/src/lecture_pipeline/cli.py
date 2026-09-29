"""Run the whole pipeline on a local video, without Temporal or the API.

    uv run lecture-process data/lectures/lecture-10.mp4 --title "Program efficiency"

On the GPU: `make process video=... title=...` runs this in the GPU worker image. Stages are
cached, so a second run only redoes what changed (a prompt, a model, a threshold).
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from lecture_core.notes import StudyNotes, to_markdown
from lecture_core.storage import LocalStorage
from lecture_llm.agents import LectureLLM, Prompts, Usage
from lecture_llm.models import LLMConfigError, make_model
from lecture_llm.settings import LLMSettings
from lecture_perception.asr import FasterWhisperTranscriber, Transcriber, WhisperConfig
from lecture_perception.media import MediaError
from lecture_perception.ocr import SlideOCR
from lecture_perception.slides import DetectorConfig
from lecture_pipeline import stages
from lecture_pipeline.cache import StageCache, StageResult
from lecture_pipeline.settings import PipelineSettings


class StageTiming(BaseModel):
    stage: str
    seconds: float
    cached: bool


class PipelineRun(BaseModel):
    video: str
    video_sha256: str
    title: str
    started_at: datetime
    llm_model: str
    asr_model: str
    asr_device: str
    stages: list[StageTiming]
    llm_usage: Usage
    slides: int
    timeline_segments: int


class PipelineResult(BaseModel):
    run: PipelineRun
    dropped: list[str]
    notes: StudyNotes


def run(
    video: Path,
    title: str,
    settings: PipelineSettings,
    llm: LectureLLM,
    transcriber: Transcriber,
    log: Callable[[str], None] = print,
) -> tuple[Path, PipelineResult]:
    started_at = datetime.now(UTC)
    store = LocalStorage(settings.pipeline_store)
    ctx = stages.Context(StageCache(store), store)
    timings: list[StageTiming] = []

    def timed[T: BaseModel](name: str, step: Callable[[], StageResult[T]]) -> StageResult[T]:
        start = time.monotonic()
        result = step()
        seconds = time.monotonic() - start
        timings.append(StageTiming(stage=name, seconds=seconds, cached=result.cached))
        log(f"  {name:<12} {'cached' if result.cached else f'{seconds:6.1f}s'}")
        return result

    log(f"Processing {video.name}")
    sha = stages.file_sha256(video)
    info = timed("probe", lambda: stages.probe(ctx, video, sha))
    audio = timed("audio", lambda: stages.audio(ctx, video, sha))
    transcript = timed("asr", lambda: stages.transcribe(ctx, audio, transcriber))
    transcriber.close()  # frees VRAM before anything else needs it
    deck = timed("slides", lambda: stages.slides(ctx, video, sha, info.output, DetectorConfig()))
    texts = timed("ocr", lambda: stages.ocr_slides(ctx, deck, SlideOCR()))
    readings = timed(
        "read_slides",
        lambda: stages.read_slides(ctx, deck, llm, texts, settings.slide_reader),
    )
    timeline = timed("timeline", lambda: stages.timeline(ctx, transcript, deck, readings))
    plan = timed("chapters", lambda: stages.chapters(ctx, timeline, llm))
    draft = timed("draft_notes", lambda: stages.draft_notes(ctx, timeline, plan, llm))
    notes = timed("notes", lambda: stages.notes(ctx, timeline, draft))

    result = PipelineResult(
        run=PipelineRun(
            video=video.as_posix(),
            video_sha256=sha,
            title=title,
            started_at=started_at,
            llm_model=llm.model_name,
            asr_model=transcriber.model_id,
            asr_device=transcriber.device,
            stages=timings,
            llm_usage=readings.output.usage + plan.output.usage + draft.output.usage,
            slides=len(deck.output.slides),
            timeline_segments=len(timeline.output.segments),
        ),
        dropped=notes.output.dropped,
        notes=notes.output.notes,
    )
    run_dir = settings.pipeline_runs_dir / video.stem / f"{started_at:%Y%m%dT%H%M%SZ}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "notes.md").write_text(to_markdown(result.notes, title), encoding="utf-8")
    return run_dir, result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lecture-process", description="Run the pipeline on a local lecture video."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--title", help="lecture title (default: the file name)")
    args = parser.parse_args(argv)
    if not args.video.is_file():
        parser.error(f"no such file: {args.video}")

    settings = PipelineSettings()
    llm_settings = LLMSettings()
    try:
        llm = LectureLLM(
            make_model(llm_settings),
            Prompts.load(llm_settings.prompts_dir),
            llm_settings.slides_per_request,
        )
    except LLMConfigError as error:
        print(error, file=sys.stderr)
        return 2
    transcriber = FasterWhisperTranscriber(
        WhisperConfig(
            model_path=settings.whisper_model_path,
            model_id=settings.whisper_model_id,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            language=settings.whisper_language,
        )
    )
    try:
        run_dir, result = run(args.video, args.title or args.video.stem, settings, llm, transcriber)
    except MediaError as error:
        print(error, file=sys.stderr)
        return 1

    record, notes = result.run, result.notes
    usage = record.llm_usage
    print(f"Saved to {run_dir}")
    print(
        f"ASR: {record.asr_model} on {record.asr_device}. "
        f"{record.slides} slides, {record.timeline_segments} timeline segments."
    )
    print(
        f"LLM: {record.llm_model}, {usage.requests} requests, {usage.input_tokens:,} in, "
        f"{usage.output_tokens:,} out"
    )
    print(
        f"Notes: {len(notes.chapters)} chapters, {len(notes.concepts)} concepts, "
        f"{len(notes.formulas)} formulas, {len(notes.quiz)} quiz questions"
        + (f"; {len(result.dropped)} items dropped" if result.dropped else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
