"""The pipeline's four LLM steps: read slides, plan chapters, write chapter notes, write the
overview. Outputs cite timeline segment ids, which the pipeline turns into timestamps.

Slide text and transcripts are untrusted: a slide can carry a prompt-injection string. They go
into delimited blocks, HTML-escaped so they can't close a block, and every prompt tells the
model they are content, never instructions (docs/blueprint.md, section 5).
"""

import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from lecture_core.latex import LaTeX
from lecture_core.notes import format_timestamp
from lecture_core.timeline import SlideReading, Timeline, TimelineSegment


class Usage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: RunUsage) -> None:
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str

    @classmethod
    def load(cls, prompts_dir: Path, name: str) -> "Prompt":
        return cls(name, (prompts_dir / f"{name}.md").read_text(encoding="utf-8"))

    @property
    def fingerprint(self) -> str:
        """Goes into stage cache keys, so editing a prompt re-runs only its stages."""
        return f"{self.name}@{hashlib.sha256(self.text.encode()).hexdigest()[:12]}"


# What the model returns. Each item cites a segment id rather than a time.


class _SlideOut(BaseModel):
    slide_id: int
    title: str
    text: str
    figure_description: str
    latex: list[LaTeX]
    code: str


class _SlideBatch(BaseModel):
    slides: list[_SlideOut]


class PlannedChapter(BaseModel):
    title: str
    first_segment: str = Field(description="Id of the chapter's first segment, e.g. s012")


class _ChapterPlan(BaseModel):
    chapters: list[PlannedChapter] = Field(min_length=1)


class CitedConcept(BaseModel):
    term: str
    definition: str
    segment: str


class CitedFormula(BaseModel):
    latex: LaTeX
    meaning: str
    segment: str


class ChapterNotes(BaseModel):
    summary: str
    concepts: list[CitedConcept]
    formulas: list[CitedFormula]


class CitedQuestion(BaseModel):
    question: str
    answer: str
    segment: str


class Overview(BaseModel):
    tldr: str
    quiz: list[CitedQuestion]


@dataclass(frozen=True)
class Prompts:
    read_slides: Prompt
    chapters: Prompt
    chapter_notes: Prompt
    overview: Prompt

    @classmethod
    def load(cls, prompts_dir: Path) -> "Prompts":
        return cls(
            read_slides=Prompt.load(prompts_dir, "read-slides.v2"),
            chapters=Prompt.load(prompts_dir, "chapters.v1"),
            chapter_notes=Prompt.load(prompts_dir, "chapter-notes.v1"),
            overview=Prompt.load(prompts_dir, "overview.v1"),
        )


class LectureLLM:
    def __init__(self, model: Model, prompts: Prompts, slides_per_request: int = 8) -> None:
        self.model_name = f"{model.system}:{model.model_name}"
        self.prompts = prompts
        self.slides_per_request = slides_per_request
        self._slides = Agent(model, output_type=_SlideBatch, instructions=prompts.read_slides.text)
        self._chapters = Agent(model, output_type=_ChapterPlan, instructions=prompts.chapters.text)
        self._notes = Agent(
            model, output_type=ChapterNotes, instructions=prompts.chapter_notes.text
        )
        self._overview = Agent(model, output_type=Overview, instructions=prompts.overview.text)

    def read_slides(self, images: Sequence[tuple[int, bytes]]) -> tuple[list[SlideReading], Usage]:
        """Batches several slides per request. A slide the model skips gets one retry on its
        own, then an empty reading, so one bad slide never fails the lecture."""
        usage = Usage()
        readings: dict[int, SlideReading] = {}
        pending = list(images)
        for batch_size in (self.slides_per_request, 1):
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                prompt: list[str | BinaryContent] = []
                for slide_id, jpeg in batch:
                    prompt += [
                        f"Slide id {slide_id}:",
                        BinaryContent(jpeg, media_type="image/jpeg"),
                    ]
                result = self._slides.run_sync(prompt)
                usage.add(result.usage)
                wanted = {slide_id for slide_id, _ in batch}
                for out in result.output.slides:
                    if out.slide_id in wanted:
                        readings[out.slide_id] = SlideReading(**out.model_dump())
            pending = [(slide_id, jpeg) for slide_id, jpeg in pending if slide_id not in readings]
        for slide_id, _ in pending:
            readings[slide_id] = SlideReading(
                slide_id=slide_id, title="", text="", figure_description="", latex=[], code=""
            )
        return [readings[slide_id] for slide_id, _ in images], usage

    def plan_chapters(self, timeline: Timeline) -> tuple[list[PlannedChapter], Usage]:
        result = self._chapters.run_sync(render(timeline, timeline.segments))
        usage = Usage()
        usage.add(result.usage)
        return result.output.chapters, usage

    def chapter_notes(
        self, timeline: Timeline, segments: Sequence[TimelineSegment]
    ) -> tuple[ChapterNotes, Usage]:
        result = self._notes.run_sync(render(timeline, segments))
        usage = Usage()
        usage.add(result.usage)
        return result.output, usage

    def overview(
        self, timeline: Timeline, chapters: Sequence[tuple[str, ChapterNotes]]
    ) -> tuple[Overview, Usage]:
        parts = []
        for title, notes in chapters:
            parts.append(f"<chapter title={attr(title)}>\n{html.escape(notes.summary)}")
            parts += [
                f"- {html.escape(c.term)} ({c.segment}): {html.escape(c.definition)}"
                for c in notes.concepts
            ]
            parts.append("</chapter>")
        prompt = "\n".join(parts) + "\n\n" + render(timeline, timeline.segments)
        result = self._overview.run_sync(prompt)
        usage = Usage()
        usage.add(result.usage)
        return result.output, usage


def render(timeline: Timeline, segments: Sequence[TimelineSegment]) -> str:
    """Segments as delimited blocks. A slide's content appears once, where it first shows."""
    lines: list[str] = []
    shown: int | None = None
    for segment in segments:
        span = f"{format_timestamp(segment.start_s)}-{format_timestamp(segment.end_s)}"
        lines.append(f'<segment id="{segment.id}" time="{span}">')
        slide = timeline.slide(segment.slide_id)
        if slide is not None and segment.slide_id != shown:
            lines.append(f'<slide id="{slide.slide_id}">\n{render_slide(slide)}\n</slide>')
            shown = segment.slide_id
        elif slide is not None:
            lines.append(f'<slide id="{slide.slide_id}">(same slide as before)</slide>')
        lines.append(f"<speech>{html.escape(segment.transcript)}</speech>")
        lines.append("</segment>")
    return "\n".join(lines)


def render_slide(slide: SlideReading) -> str:
    """A slide's reading as labelled lines, HTML-escaped."""
    fields = [
        ("Title", slide.title),
        ("Text", slide.text),
        ("Figure", slide.figure_description),
        ("LaTeX", "; ".join(slide.latex)),
        ("Code", slide.code),
    ]
    return "\n".join(f"{name}: {html.escape(value)}" for name, value in fields if value)


def attr(value: str) -> str:
    """An HTML-escaped, quoted attribute value."""
    return '"' + html.escape(value, quote=True) + '"'
