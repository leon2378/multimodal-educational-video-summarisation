"""Questions about a lecture or a course, answered from retrieved passages (docs/blueprint.md,
section 5).

An answer cites the lecture as [mm:ss], copying the time of the sentence it draws on from the
passages it was given. Across a course, each lecture gets a label and citations name it:
[L2 12:34]. Every citation is then checked against the passages: one that points outside them
wasn't grounded in anything retrieved.
"""

import re
import uuid
from collections.abc import Sequence

from pydantic import BaseModel

from lecture_core.notes import parse_timestamp
from lecture_core.timeline import SlideReading

# [mm:ss] or [h:mm:ss], as format_timestamp writes them, optionally after a lecture label, or
# several in one bracket, which models sometimes write despite being asked not to:
# [12:34, 12:50] or [L1 12:34, L2 03:10]. A time without a label takes the one before it.
_ITEM = r"(?:L\d+\s+)?\d{1,3}:\d{2}(?::\d{2})?"
_CITATION = re.compile(rf"\[({_ITEM}(?:\s*[,;]\s*{_ITEM})*)\]")
_SEPARATOR = re.compile(r"\s*[,;]\s*")
_LABELLED = re.compile(r"(?:(L\d+)\s+)?(\S+)")
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
    # In an answer across a course: the lecture's title and its label there, e.g. "L2".
    lecture_title: str | None = None
    label: str | None = None


class Citation(BaseModel):
    label: str
    at_s: float
    # The passage it points into; None when it points outside every passage.
    lecture_id: uuid.UUID | None
    segment_id: str | None
    valid: bool


class ChatTurn(BaseModel):
    question: str
    answer: str


def label_lectures(passages: Sequence[Passage], titles: dict[uuid.UUID, str]) -> list[Passage]:
    """Label each lecture L1, L2, ... in the order the passages first mention it."""
    labels: dict[uuid.UUID, str] = {}
    for passage in passages:
        labels.setdefault(passage.lecture_id, f"L{len(labels) + 1}")
    return [
        p.model_copy(
            update={"label": labels[p.lecture_id], "lecture_title": titles.get(p.lecture_id)}
        )
        for p in passages
    ]


def find_citations(answer: str, passages: Sequence[Passage]) -> list[Citation]:
    """Each distinct citation in the answer, in order, checked against the passages."""
    citations: dict[str, Citation] = {}
    for match in _CITATION.finditer(answer):
        lecture: str | None = None
        for item in _SEPARATOR.split(match.group(1)):
            parts = _LABELLED.fullmatch(item)
            if parts is None:
                continue
            lecture = parts.group(1) or lecture
            time = parts.group(2)
            label = f"[{lecture} {time}]" if lecture else f"[{time}]"
            if label in citations:
                continue
            try:
                at_s = parse_timestamp(time)
            except ValueError:
                continue
            passage = next(
                (
                    p
                    for p in passages
                    if (lecture is None or p.label == lecture)
                    and p.start_s - _TOLERANCE_S <= at_s <= p.end_s + _TOLERANCE_S
                ),
                None,
            )
            citations[label] = Citation(
                label=label,
                at_s=at_s,
                lecture_id=passage.lecture_id if passage else None,
                segment_id=passage.segment_id if passage else None,
                valid=passage is not None,
            )
    return list(citations.values())
