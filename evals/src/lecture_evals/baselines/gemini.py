"""Single-call Gemini baseline: the whole lecture video in, study notes out.

Answers "why not just send the video to Gemini?" with numbers. The notes use the same format
as the pipeline (lecture_core.notes.StudyNotes), so the eval suites can score both, and each
run records tokens, cost and timings.

    uv run gemini-baseline data/lectures/lecture-15.mp4 --title "Dynamic programming"

Needs GEMINI_API_KEY in .env or the environment. Google may use content sent through the free
tier to improve its products, so on the free tier only run this on public, openly licensed
lectures such as MIT OpenCourseWare.
"""

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from lecture_core.notes import (
    Chapter,
    Concept,
    Formula,
    QuizQuestion,
    StudyNotes,
    format_timestamp,
    parse_timestamp,
)
from lecture_evals.checks import NotesChecks, check_notes
from lecture_evals.pricing import Price, TokenUsage, cost_usd, price_for
from lecture_evals.report import notes_to_markdown

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_PROMPT = Path("prompts/baseline-gemini/v1.md")
DEFAULT_OUT = Path("data/baselines")

PROCESSING = {"static": types.MediaProcessing.STATIC, "agentic": types.MediaProcessing.AGENTIC}
RESOLUTION = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None


# What the model fills in. Timestamps stay as text here because Gemini works with video
# time as MM:SS. They're converted to seconds (StudyNotes) afterwards.
_TIMESTAMP = "MM:SS, or H:MM:SS from the first hour on"


class _Chapter(BaseModel):
    title: str
    start: str = Field(description=f"Where the chapter starts: {_TIMESTAMP}")
    end: str = Field(description=f"Where the chapter ends: {_TIMESTAMP}")
    summary: str


class _Concept(BaseModel):
    term: str
    definition: str
    timestamp: str = Field(description=f"Where it is first explained: {_TIMESTAMP}")


class _Formula(BaseModel):
    latex: str = Field(description="LaTeX without surrounding $ signs")
    meaning: str
    timestamp: str = Field(description=f"Where it is written or derived: {_TIMESTAMP}")


class _QuizQuestion(BaseModel):
    question: str
    answer: str
    timestamp: str = Field(description=f"Where the lecture covers the answer: {_TIMESTAMP}")


class GeminiNotes(BaseModel):
    tldr: str
    chapters: list[_Chapter] = Field(min_length=1)
    concepts: list[_Concept]
    formulas: list[_Formula]
    quiz: list[_QuizQuestion]


class RunRecord(BaseModel):
    baseline: Literal["gemini-single-call"] = "gemini-single-call"
    model: str
    model_version: str | None
    prompt: str
    prompt_sha256: str
    processing: str
    media_resolution: str
    fps: float | None
    video: str
    video_sha256: str
    duration_s: float | None
    title: str
    started_at: datetime
    file_reused: bool
    timings_s: dict[str, float | None]
    usage: TokenUsage
    price: Price | None
    cost_usd: float | None
    finish_reason: str | None
    # More than 1 when Gemini returned a transient error (e.g. 503 high demand) and we retried.
    generation_attempts: int
    sdk_version: str


class BaselineResult(BaseModel):
    run: RunRecord
    checks: NotesChecks
    # Items dropped because their timestamp couldn't be read.
    dropped: list[str]
    notes: StudyNotes


@dataclass(frozen=True)
class Options:
    video: Path
    title: str
    model: str = DEFAULT_MODEL
    prompt: Path = DEFAULT_PROMPT
    processing: str = "static"
    fps: float | None = 1.0
    media_resolution: str = "low"
    max_output_tokens: int = 65_536
    out_root: Path = DEFAULT_OUT
    price_override: Price | None = None
    poll_s: float = 5.0
    # First wait before retrying a failed generation; doubles each time.
    retry_delay_s: float = 15.0


@dataclass(frozen=True)
class Generation:
    text: str
    usage: TokenUsage
    finish_reason: str | None
    model_version: str | None
    first_token_s: float | None
    total_s: float


def run_baseline(client: genai.Client, options: Options) -> tuple[Path, BaselineResult]:
    started_at = datetime.now(UTC)
    prompt = options.prompt.read_text(encoding="utf-8")
    video_sha256 = _sha256(options.video)
    duration_s = _probe_duration(options.video)

    upload_started = time.monotonic()
    file, reused = _upload(
        client, options.video, video_sha256, options.out_root / ".gemini-files.json"
    )
    upload_s = time.monotonic() - upload_started
    processing_started = time.monotonic()
    file = _wait_until_active(client, file, options.poll_s)
    processing_s = time.monotonic() - processing_started
    duration_s = duration_s or _duration_from_file(file)

    context = f"\n\nLecture title: {options.title}"
    if duration_s is not None:
        context += f"\nVideo length: {format_timestamp(duration_s)}"
    generation, attempts = _generate_with_retries(client, file, prompt + context, options)

    run_dir = (
        options.out_root
        / options.video.stem
        / f"{started_at:%Y%m%dT%H%M%SZ}-{options.model}-{options.processing}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    # Saved before parsing, so a malformed response can still be inspected.
    (run_dir / "response.json").write_text(generation.text, encoding="utf-8")
    if generation.finish_reason != types.FinishReason.STOP.name:
        raise RuntimeError(
            f"generation stopped early ({generation.finish_reason}); raw output in {run_dir}"
        )
    try:
        raw = GeminiNotes.model_validate_json(generation.text)
    except ValidationError as error:
        raise RuntimeError(f"response doesn't match the schema; raw output in {run_dir}") from error
    notes, dropped = to_study_notes(raw)

    price = options.price_override or price_for(options.model, started_at.date())
    result = BaselineResult(
        run=RunRecord(
            model=options.model,
            model_version=generation.model_version,
            prompt=options.prompt.as_posix(),
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            processing=options.processing,
            media_resolution=options.media_resolution,
            fps=options.fps if options.processing == "static" else None,
            video=options.video.as_posix(),
            video_sha256=video_sha256,
            duration_s=duration_s,
            title=options.title,
            started_at=started_at,
            file_reused=reused,
            timings_s={
                "upload": upload_s,
                "file_processing": processing_s,
                "first_token": generation.first_token_s,
                "generation": generation.total_s,
            },
            usage=generation.usage,
            price=price,
            cost_usd=cost_usd(generation.usage, price) if price else None,
            finish_reason=generation.finish_reason,
            generation_attempts=attempts,
            sdk_version=version("google-genai"),
        ),
        checks=check_notes(notes, duration_s),
        dropped=dropped,
        notes=notes,
    )
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "notes.md").write_text(notes_to_markdown(notes, options.title), encoding="utf-8")
    return run_dir, result


def to_study_notes(raw: GeminiNotes) -> tuple[StudyNotes, list[str]]:
    """Convert timestamps to seconds, dropping (and listing) items whose timestamp is unreadable."""
    dropped: list[str] = []

    def seconds(label: str, text: str) -> float | None:
        try:
            return parse_timestamp(text)
        except ValueError:
            dropped.append(f"{label}: unreadable timestamp {text!r}")
            return None

    chapters = []
    for i, c in enumerate(raw.chapters):
        start, end = seconds(f"chapters[{i}].start", c.start), seconds(f"chapters[{i}].end", c.end)
        if start is not None and end is not None:
            chapters.append(Chapter(title=c.title, start_s=start, end_s=end, summary=c.summary))
    notes = StudyNotes(
        tldr=raw.tldr,
        chapters=chapters,
        concepts=[
            Concept(term=c.term, definition=c.definition, at_s=at)
            for i, c in enumerate(raw.concepts)
            if (at := seconds(f"concepts[{i}]", c.timestamp)) is not None
        ],
        formulas=[
            Formula(latex=f.latex, meaning=f.meaning, at_s=at)
            for i, f in enumerate(raw.formulas)
            if (at := seconds(f"formulas[{i}]", f.timestamp)) is not None
        ],
        quiz=[
            QuizQuestion(question=q.question, answer=q.answer, at_s=at)
            for i, q in enumerate(raw.quiz)
            if (at := seconds(f"quiz[{i}]", q.timestamp)) is not None
        ],
    )
    return notes, dropped


def _upload(
    client: genai.Client, video: Path, sha256: str, registry: Path
) -> tuple[types.File, bool]:
    """Reuse this video's earlier upload while Gemini still has it (files expire after 48 h)."""
    known: dict[str, str] = json.loads(registry.read_text()) if registry.exists() else {}
    if name := known.get(sha256):
        try:
            existing = client.files.get(name=name)
        except errors.ClientError:
            existing = None
        if existing is not None and existing.state == types.FileState.ACTIVE:
            return existing, True

    file = client.files.upload(file=video, config=types.UploadFileConfig(display_name=video.name))
    if file.name is None:
        raise RuntimeError("upload returned no file name")
    known[sha256] = file.name
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(known, indent=2))
    return file, False


def _wait_until_active(
    client: genai.Client, file: types.File, poll_s: float, timeout_s: float = 1800
) -> types.File:
    deadline = time.monotonic() + timeout_s
    while file.state != types.FileState.ACTIVE:
        if file.state == types.FileState.FAILED:
            raise RuntimeError(f"Gemini couldn't process the video: {file.error}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"video still processing after {timeout_s:.0f}s")
        time.sleep(poll_s)
        file = client.files.get(name=file.name or "")
    return file


_TRANSIENT = {429, 500, 502, 503, 504}
_GENERATION_ATTEMPTS = 4


def _generate_with_retries(
    client: genai.Client, file: types.File, prompt: str, options: Options
) -> tuple[Generation, int]:
    """The SDK retries a request that fails outright, but not a stream that fails partway
    (Gemini can return 503 "high demand" mid-stream), so retry the whole generation."""
    for attempt in range(1, _GENERATION_ATTEMPTS + 1):
        try:
            return _generate(client, file, prompt, options), attempt
        except errors.APIError as error:
            if error.code not in _TRANSIENT or attempt == _GENERATION_ATTEMPTS:
                raise
            delay = options.retry_delay_s * 2 ** (attempt - 1)
            print(
                f"Gemini error {error.code} ({error.status}); retrying in {delay:.0f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def _generate(client: genai.Client, file: types.File, prompt: str, options: Options) -> Generation:
    if file.uri is None:
        raise RuntimeError("uploaded file has no URI")
    static = options.processing == "static"
    video = types.Part(
        file_data=types.FileData(file_uri=file.uri, mime_type=file.mime_type),
        # Frame rate only applies to static processing; agentic mode picks its own frames.
        video_metadata=types.VideoMetadata(fps=options.fps) if static else None,
        media_processing=PROCESSING[options.processing],
    )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=GeminiNotes.model_json_schema(),
        media_resolution=RESOLUTION[options.media_resolution],
        max_output_tokens=options.max_output_tokens,
        # No tools here. Without this the SDK routes through its function-calling loop and
        # logs a warning about it.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    started = time.monotonic()
    first_token_s: float | None = None
    parts: list[str] = []
    last: types.GenerateContentResponse | None = None
    sdk_log = logging.getLogger("google_genai.types")
    quiet = _SkipNonTextWarning()
    sdk_log.addFilter(quiet)
    try:
        # Streaming keeps the connection busy during long generations over long videos.
        for chunk in client.models.generate_content_stream(
            model=options.model, contents=[video, prompt], config=config
        ):
            if text := _text(chunk):
                if first_token_s is None:
                    first_token_s = time.monotonic() - started
                parts.append(text)
            last = chunk
    finally:
        sdk_log.removeFilter(quiet)
    total_s = time.monotonic() - started

    if last is None:
        raise RuntimeError("empty response from Gemini")
    finish = last.candidates[0].finish_reason if last.candidates else None
    return Generation(
        text="".join(parts),
        usage=_usage(last.usage_metadata),
        finish_reason=finish.name if finish else None,
        model_version=last.model_version,
        first_token_s=first_token_s,
        total_s=total_s,
    )


class _SkipNonTextWarning(logging.Filter):
    """In agentic mode the stream also carries the model's tool calls (moving around the
    video). With a JSON schema set, the SDK tries to parse each chunk itself and logs a
    warning about those non-text parts. _text() reads the text parts directly, so it's noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "non-text parts in the response" not in record.getMessage()


def _text(chunk: types.GenerateContentResponse) -> str:
    """The answer's text, skipping thoughts and agentic mode's tool calls."""
    if not chunk.candidates or chunk.candidates[0].content is None:
        return ""
    parts = chunk.candidates[0].content.parts or []
    return "".join(part.text for part in parts if part.text and not part.thought)


def _usage(meta: types.GenerateContentResponseUsageMetadata | None) -> TokenUsage:
    if meta is None:
        raise RuntimeError("response has no usage metadata")
    return TokenUsage(
        prompt_total=meta.prompt_token_count or 0,
        prompt_by_modality={
            detail.modality.name: detail.token_count or 0
            for detail in meta.prompt_tokens_details or []
            if detail.modality is not None
        },
        tool_use_prompt=meta.tool_use_prompt_token_count or 0,
        output=meta.candidates_token_count or 0,
        thinking=meta.thoughts_token_count or 0,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_duration(video: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _duration_from_file(file: types.File) -> float | None:
    """Fallback when ffprobe isn't installed: Gemini reports e.g. {"videoDuration": "3600s"}."""
    for key, value in (file.video_metadata or {}).items():
        if "duration" in key.lower():
            try:
                return float(str(value).removesuffix("s"))
            except ValueError:
                return None
    return None


def _parse_args(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(
        prog="gemini-baseline",
        description="Summarise a lecture video with one Gemini call and record cost and timings.",
    )
    parser.add_argument("video", type=Path, help="lecture video file (mp4, mov, webm, ...)")
    parser.add_argument("--title", help="lecture title (default: the file name)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"default: {DEFAULT_MODEL}")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT, help="prompt file")
    parser.add_argument(
        "--processing",
        choices=PROCESSING,
        default="static",
        help="static puts every frame in context (default); agentic lets the model pick",
    )
    parser.add_argument(
        "--fps", type=float, default=None, help="frames per second, static only (default 1)"
    )
    parser.add_argument("--media-resolution", choices=RESOLUTION, default="low")
    parser.add_argument("--max-output-tokens", type=int, default=65_536)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output root directory")
    parser.add_argument("--input-price", type=float, help="USD per 1M input tokens")
    parser.add_argument("--output-price", type=float, help="USD per 1M output tokens")
    args = parser.parse_args(argv)

    if not args.video.is_file():
        parser.error(f"no such file: {args.video}")
    if args.fps is not None and args.processing != "static":
        parser.error("--fps only applies to --processing static")
    if (args.input_price is None) != (args.output_price is None):
        parser.error("pass --input-price and --output-price together")
    override = (
        Price(input=args.input_price, output=args.output_price)
        if args.input_price is not None
        else None
    )
    return Options(
        video=args.video,
        title=args.title or args.video.stem,
        model=args.model,
        prompt=args.prompt,
        processing=args.processing,
        fps=args.fps if args.fps is not None else 1.0,
        media_resolution=args.media_resolution,
        max_output_tokens=args.max_output_tokens,
        out_root=args.out,
        price_override=override,
    )


def main(argv: Sequence[str] | None = None) -> int:
    options = _parse_args(argv)
    key = GeminiSettings().gemini_api_key
    if key is None:
        print("Set GEMINI_API_KEY in .env or the environment.", file=sys.stderr)
        return 2
    client = genai.Client(
        api_key=key.get_secret_value(),
        http_options=types.HttpOptions(
            timeout=20 * 60 * 1000,  # milliseconds
            retry_options=types.HttpRetryOptions(
                attempts=4, http_status_codes=[429, 500, 502, 503, 504]
            ),
        ),
    )
    try:
        run_dir, result = run_baseline(client, options)
    except errors.APIError as error:
        print(f"Gemini API error {error.code}: {error.message}", file=sys.stderr)
        return 1
    except (RuntimeError, TimeoutError) as error:
        print(error, file=sys.stderr)
        return 1

    run, checks = result.run, result.checks
    cost = f"${run.cost_usd:.3f}" if run.cost_usd is not None else "unknown (no price for model)"
    coverage = f"{checks.chapter_coverage:.0%}" if checks.chapter_coverage is not None else "n/a"
    print(f"Saved to {run_dir}")
    print(
        f"Tokens: {run.usage.prompt_total:,} in ({run.usage.prompt_by_modality}), "
        f"{run.usage.output:,} out, {run.usage.thinking:,} thinking. Cost: {cost}"
    )
    print(
        "Time: "
        + ", ".join(f"{k} {v:.1f}s" for k, v in run.timings_s.items() if v is not None)
        + (" (upload reused)" if run.file_reused else "")
    )
    print(
        f"Notes: {len(result.notes.chapters)} chapters, {len(result.notes.concepts)} concepts, "
        f"{len(result.notes.formulas)} formulas, {len(result.notes.quiz)} quiz questions"
    )
    print(
        f"Checks: {checks.timestamps} timestamps, {checks.out_of_range} outside the video, "
        f"{len(result.dropped)} unreadable, chapter coverage {coverage}, "
        f"{len(checks.issues)} issues"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
