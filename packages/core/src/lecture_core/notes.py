"""Study notes: the shared output format for anything that summarises a lecture.

The pipeline (Phase 2) and every baseline write this format, so eval suites can score them
the same way. Times are seconds from the start of the video.
"""

import math
import re

from pydantic import BaseModel


class Chapter(BaseModel):
    title: str
    start_s: float
    end_s: float
    summary: str


class Concept(BaseModel):
    term: str
    definition: str
    at_s: float


class Formula(BaseModel):
    latex: str
    meaning: str
    at_s: float


class QuizQuestion(BaseModel):
    question: str
    answer: str
    at_s: float


class StudyNotes(BaseModel):
    tldr: str
    chapters: list[Chapter]
    concepts: list[Concept]
    formulas: list[Formula]
    quiz: list[QuizQuestion]


_TIMESTAMP = re.compile(r"\[?\s*(?:(\d+):)?(\d+):(\d{1,2}(?:\.\d+)?)\s*\]?")


def parse_timestamp(text: str) -> float:
    """Seconds from "MM:SS" or "H:MM:SS", with optional brackets and fractional seconds.

    Minutes may exceed 59 when there's no hour field ("75:10"), since models often write
    long-video times that way.
    """
    match = _TIMESTAMP.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"not a timestamp: {text!r}")
    hours, minutes, seconds = match.groups()
    if float(seconds) >= 60 or (hours is not None and int(minutes) >= 60):
        raise ValueError(f"not a timestamp: {text!r}")
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float) -> str:
    """Format as MM:SS under an hour and H:MM:SS from an hour on, rounding down."""
    whole = math.floor(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
