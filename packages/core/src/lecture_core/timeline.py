"""The lecture timeline: what was said, what was shown, and when.

Summaries, search and the UI all read from this one time-aligned model (docs/blueprint.md,
section 3). Times are seconds from the start of the video.
"""

import re
from typing import Literal

from pydantic import BaseModel, model_validator

from lecture_core.latex import Formulas, PlainText


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
    """What a slide says. A reading is tidied whenever it's read, from the model, the cache or
    the database (lecture_core.latex): LaTeX in its text is made readable, a formula listed
    twice is listed once, text that starts by repeating the title, shown above it, doesn't, and
    OCR's 0(n) for O(n) is put right."""

    slide_id: int
    title: PlainText
    text: PlainText
    figure_description: PlainText
    latex: Formulas
    code: str
    # Who read it: the vision LLM, or OCR alone (slides with only text; see lecture_perception.ocr).
    reader: Literal["vlm", "ocr"] = "vlm"

    @model_validator(mode="after")
    def _tidy(self) -> "SlideReading":
        first, _, rest = self.text.partition("\n")
        if self.title and first.strip().casefold() == self.title.strip().casefold():
            self.text = rest.lstrip("\n")
        if self.reader == "ocr":
            self.title = _BIG_O_MISREAD.sub("O", self.title)
            self.text = _BIG_O_MISREAD.sub("O", self.text)
        return self


# OCR reads big-O notation's O as a zero: 0(1), 0(n log n), 0(len(L)). Not after a letter,
# digit or point, so f(0) and 10(1) stay as they are.
_BIG_O_MISREAD = re.compile(r"(?<![\w.])0(?=\([^()\n]{0,20}(?:\([^()\n]{0,20}\)[^()\n]{0,10})?\))")


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
