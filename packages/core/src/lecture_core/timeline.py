"""The lecture timeline: what was said, what was shown, and when.

Summaries, search and the UI all read from this one time-aligned model (docs/blueprint.md,
section 3). Times are seconds from the start of the video.
"""

from typing import Literal

from pydantic import BaseModel


class Word(BaseModel):
    start_s: float
    end_s: float
    text: str
    probability: float


class TranscriptSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    words: list[Word]


class Transcript(BaseModel):
    language: str
    duration_s: float
    segments: list[TranscriptSegment]


class SlideImage(BaseModel):
    """One distinct slide. Shown again later, it keeps the same id."""

    id: int
    image_key: str
    first_seen_s: float


class SlideSpan(BaseModel):
    """A stretch of video during which `slide_id` is the current slide, including camera shots
    of the lecturer until the next slide appears."""

    slide_id: int
    start_s: float
    end_s: float


class SlideDeck(BaseModel):
    slides: list[SlideImage]
    spans: list[SlideSpan]
    # Share of sampled frames that showed a slide rather than the camera.
    slide_share: float


class SlideReading(BaseModel):
    slide_id: int
    title: str
    text: str
    figure_description: str
    latex: list[str]
    code: str
    # Who read it: the vision LLM, or OCR alone (slides with only text; see lecture_perception.ocr).
    reader: Literal["vlm", "ocr"] = "vlm"


class TimelineSegment(BaseModel):
    """The unit everything cites: one slide's worth of speech, split if it runs long."""

    id: str
    start_s: float
    end_s: float
    transcript: str
    slide_id: int | None
    words: list[Word]


class Timeline(BaseModel):
    duration_s: float
    segments: list[TimelineSegment]
    slides: list[SlideReading]

    def slide(self, slide_id: int | None) -> SlideReading | None:
        return next((s for s in self.slides if s.slide_id == slide_id), None)
