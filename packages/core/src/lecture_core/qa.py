"""Questions about a lecture, answered from retrieved passages (docs/blueprint.md, section 5).

An answer cites the lecture as [mm:ss], copying the time of the sentence it draws on from the
passages it was given. Every citation is then checked against those passages: one that points
outside them wasn't grounded in anything retrieved.
"""

import re
import uuid
from collections.abc import Sequence

from pydantic import BaseModel

from lecture_core.notes import parse_timestamp
from lecture_core.timeline import SlideReading

# [mm:ss] or [h:mm:ss], as format_timestamp writes them, or several in one bracket, which
# models sometimes write despite being asked not to: [12:34, 12:50].
_TIME = r"\d{1,3}:\d{2}(?::\d{2})?"
_CITATION = re.compile(rf"\[({_TIME}(?:\s*[,;]\s*{_TIME})*)\]")
_SEPARATOR = re.compile(r"\s*[,;]\s*")
# Timestamps are shown rounded down to the second.
_TOLERANCE_S = 1.0


class Sentence(BaseModel):
    start_s: float
    text: str


class Passage(BaseModel):
    """One retrieved timeline segment, as the model sees it."""

    lecture_id: uuid.UUID
    segment_id: str
    start_s: float
    end_s: float
    chapter: str | None
    slide: SlideReading | None
    sentences: list[Sentence]


class Citation(BaseModel):
    label: str
    at_s: float
    # The passage it points into; None when it points outside every passage.
    segment_id: str | None
    valid: bool


class ChatTurn(BaseModel):
    question: str
    answer: str


def find_citations(answer: str, passages: Sequence[Passage]) -> list[Citation]:
    """Each distinct [mm:ss] in the answer, in order, checked against the passages."""
    citations: dict[str, Citation] = {}
    for match in _CITATION.finditer(answer):
        for time in _SEPARATOR.split(match.group(1)):
            label = f"[{time}]"
            if label in citations:
                continue
            try:
                at_s = parse_timestamp(time)
            except ValueError:
                continue
            passage = next(
                (p for p in passages if p.start_s - _TOLERANCE_S <= at_s <= p.end_s + _TOLERANCE_S),
                None,
            )
            citations[label] = Citation(
                label=label,
                at_s=at_s,
                segment_id=passage.segment_id if passage else None,
                valid=passage is not None,
            )
    return list(citations.values())
