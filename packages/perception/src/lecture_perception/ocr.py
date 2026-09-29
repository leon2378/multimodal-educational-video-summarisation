"""OCR for slides, and the rule that decides which slides still need the vision LLM.

RapidOCR runs PP-OCRv6 models on ONNX Runtime, on the CPU (about half a second a slide at
480x360). On MIT 6.0001 Lecture 10 it recovers slide text as well as the vision LLM does (word
F1 0.82 against the slide PDF, vs 0.80), tables included. What it can't give is a description
of a plot or diagram, LaTeX, or the lecturer's annotations told apart from the slide text. So a
slide goes to the vision LLM when it shows something besides text:

- ink outside the text lines, beyond what every slide in the deck has (the template's bands and
  rules): plots, diagrams, tables drawn with lines, marks;
- text lines at an angle: annotations written over the slide;
- low OCR confidence, or almost no text (a picture).

The Phase 5 detector replaces the ink measure with boxes for figures, tables and equations.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel
from rapidocr import RapidOCR
from rapidocr.utils.output import RapidOCROutput

from lecture_core.timeline import SlideReading

Point = tuple[float, float]


class OcrLine(BaseModel):
    text: str
    score: float
    # Corners clockwise from the top left, in pixels.
    box: list[Point]

    @property
    def top(self) -> float:
        return min(y for _, y in self.box)

    @property
    def height(self) -> float:
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = self.box
        return (math.dist((x0, y0), (x3, y3)) + math.dist((x1, y1), (x2, y2))) / 2

    @property
    def angle(self) -> float:
        """Degrees from horizontal, along the line's top edge."""
        (x0, y0), (x1, y1) = self.box[0], self.box[1]
        return math.degrees(math.atan2(y1 - y0, x1 - x0))


class SlideOcr(BaseModel):
    slide_id: int
    lines: list[OcrLine]
    # The slide within the frame (left, top, right, bottom): the frame can have bars beside it.
    area: tuple[int, int, int, int]
    # Share of the slide that's ink but not text: figures, but also the template's decoration.
    ink_outside_text: float


class DeckOcr(BaseModel):
    slides: list[SlideOcr]
    model: str


class SlideOCR:
    """RapidOCR with its bundled PP-OCRv6 models, loaded on first use."""

    def __init__(self) -> None:
        self._engine: RapidOCR | None = None

    @property
    def model_id(self) -> str:
        return f"rapidocr-{version('rapidocr')}/PP-OCRv6-small"

    def read(self, slide_id: int, image: Image.Image) -> SlideOcr:
        if self._engine is None:
            self._engine = RapidOCR(
                params={
                    "Global.log_level": "warning",
                    # ONNX Runtime's default, a thread per core, was slower than 4 on a 16-thread
                    # CPU (15.8 s vs 12.3 s for 24 slides), and the worker runs other activities.
                    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                }
            )
        image = image.convert("RGB")
        # OpenCV's channel order, which RapidOCR expects of an array.
        result = self._engine(np.ascontiguousarray(np.asarray(image)[:, :, ::-1]))
        if not isinstance(result, RapidOCROutput):
            raise TypeError(f"expected a full OCR result, got {type(result).__name__}")
        lines = []
        if result.boxes is not None and result.txts is not None and result.scores is not None:
            for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
                corners = [(float(x), float(y)) for x, y in box]
                lines.append(OcrLine(text=text, score=float(score), box=corners))
        area = slide_area(image)
        return SlideOcr(
            slide_id=slide_id,
            lines=lines,
            area=area,
            ink_outside_text=ink_outside_text(image, lines, area),
        )


def slide_area(image: Image.Image) -> tuple[int, int, int, int]:
    """The bounding box of the bright slide: rows and columns that are mostly light."""
    gray = np.asarray(image.convert("L"), dtype=np.float32) / 255
    bright = gray > 0.85
    cols = np.flatnonzero(bright.mean(axis=0) > 0.3)
    rows = np.flatnonzero(bright.mean(axis=1) > 0.3)
    if cols.size == 0 or rows.size == 0:
        return (0, 0, image.width, image.height)
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def ink_outside_text(
    image: Image.Image, lines: Sequence[OcrLine], area: tuple[int, int, int, int]
) -> float:
    """Share of the slide's pixels that are dark or strongly coloured but not in a text line."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    gray = rgb.mean(axis=2)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    ink = (gray < 0.7) | (saturation > 0.35)
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    for line in lines:
        draw.polygon(line.box, fill=255)
    # Grow the text boxes a little, so anti-aliased letter edges don't count as a figure.
    text = np.asarray(mask.filter(ImageFilter.MaxFilter(7))) > 0
    left, top, right, bottom = area
    inside = np.zeros(gray.shape, dtype=bool)
    inside[top:bottom, left:right] = True
    return float((ink & inside & ~text).sum() / max(1, inside.sum()))


@dataclass(frozen=True)
class RoutingConfig:
    # Ink outside text beyond the deck's usual amount (its 25th percentile), as a share of the
    # slide. On Lecture 10, figures measure 0.003 to 0.10 and text-only slides 0.001 or less.
    figure_ink: float = 0.003
    # This many lines at more than max_angle degrees: annotations over the slide.
    rotated_lines: int = 2
    max_angle: float = 8.0
    # Mean confidence below this, or fewer words than min_words, and OCR isn't trusted.
    min_confidence: float = 0.93
    min_words: int = 5


def route(deck: DeckOcr, config: RoutingConfig) -> dict[int, list[str]]:
    """Why each slide needs the vision LLM; slides that don't are left out."""
    if not deck.slides:
        return {}
    baseline = float(np.percentile([s.ink_outside_text for s in deck.slides], 25))
    reasons: dict[int, list[str]] = {}
    for slide in deck.slides:
        why = []
        if slide.ink_outside_text - baseline > config.figure_ink:
            why.append("figure")
        if sum(abs(line.angle) > config.max_angle for line in slide.lines) >= config.rotated_lines:
            why.append("annotations")
        words = sum(len(line.text.split()) for line in slide.lines)
        if words < config.min_words:
            why.append("little text")
        elif np.mean([line.score for line in slide.lines]) < config.min_confidence:
            why.append("low confidence")
        if why:
            reasons[slide.slide_id] = why
    return reasons


# Bullet glyphs: symbol fonts map them to the private use area, others use real bullets.
_BULLET = re.compile(r"^[\ue000-\uf8ff•▪◦●■□◆·*]+\s*")
_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")


def reading_from_ocr(ocr: SlideOcr) -> SlideReading:
    """Title and text from OCR lines. The title is the tall text starting in the top 30% of the
    slide, over one or more lines; the rest is text, one line per OCR line, bullets as "- ".
    Lines in the bottom 8% are the template's footer (course name, page number) and dropped."""
    _, top, _, bottom = ocr.area
    height = bottom - top
    lines = sorted(
        (line for line in ocr.lines if line.top < top + 0.92 * height),
        key=lambda line: (round(line.top / 4), line.box[0][0]),
    )
    title: list[OcrLine] = []
    rest = list(lines)
    if lines:
        tallest = max(line.height for line in lines)
        while rest and rest[0].height >= 0.75 * tallest:
            line = rest[0]
            starts = not title and line.top < top + 0.3 * height
            follows = bool(title) and line.top - title[-1].top < 2 * title[-1].height
            if not (starts or follows):
                break
            title.append(rest.pop(0))
    text = []
    for line in rest:
        bullet, cleaned = _clean(line.text)
        if cleaned:
            text.append(f"- {cleaned}" if bullet else cleaned)
    return SlideReading(
        slide_id=ocr.slide_id,
        title=" ".join(cleaned for _, cleaned in (_clean(line.text) for line in title) if cleaned),
        text="\n".join(text),
        figure_description="",
        latex=[],
        code="",
        reader="ocr",
    )


def _clean(text: str) -> tuple[bool, str]:
    """Whether the line starts with a bullet, and its text without it."""
    text = unicodedata.normalize("NFKC", text).strip()
    return bool(_BULLET.match(text)), _PRIVATE_USE.sub("", _BULLET.sub("", text)).strip()
