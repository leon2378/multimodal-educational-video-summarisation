"""Questions about a lecture or a course, answered from retrieved passages (docs/blueprint.md,
section 5).

An answer cites the lecture as [mm:ss], copying the time of the sentence it draws on from the
passages it was given. Across a course, each lecture gets a label and citations name it:
[L2 12:34]. Every citation is then checked against the passages: one that points outside them
wasn't grounded in anything retrieved.

Search finds passages by meaning, so it can't answer a question about a lecture's order, like
its last topic: "last" has no meaning to match, and the passages it finds tend to be the
introduction, which previews everything. So an answer about one lecture also gets the
lecture's outline, its chapters with their start times, and a citation of a chapter's start is
grounded too.
"""

import re
import uuid
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from lecture_core.notes import Chapter, parse_timestamp
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


class Outline(BaseModel):
    """A lecture's summary and its chapters in order, each with its own summary, from its study
    notes: what a question about the whole lecture, or its order, is answered from."""

    lecture_id: uuid.UUID
    summary: str = ""
    chapters: list[Chapter]


class Citation(BaseModel):
    label: str
    at_s: float
    # The passage it points into. A citation of a chapter's start in the outline has the
    # lecture but no segment; one that points at neither has neither and isn't valid.
    lecture_id: uuid.UUID | None
    segment_id: str | None
    valid: bool


class ChatTurn(BaseModel):
    question: str
    answer: str


def label_lectures(
    passages: Sequence[Passage], titles: Mapping[uuid.UUID, str], numbers: Mapping[uuid.UUID, int]
) -> list[Passage]:
    """Label each lecture by its number in the course, as the course page numbers them: L3 is
    the third lecture listed there. Numbered in the order the passages mentioned them, L2 had
    been the second lecture cited, which the course page calls something else."""
    return [
        p.model_copy(
            update={"label": f"L{numbers[p.lecture_id]}", "lecture_title": titles.get(p.lecture_id)}
        )
        for p in passages
    ]


def find_citations(
    answer: str, passages: Sequence[Passage], outline: Outline | None = None
) -> list[Citation]:
    """Each distinct citation in the answer, in order, checked against the passages and the
    starts of the outline's chapters."""
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
            lecture_id: uuid.UUID | None = None
            if passage is not None:
                lecture_id = passage.lecture_id
            elif (
                lecture is None
                and outline is not None
                and any(abs(c.start_s - at_s) <= _TOLERANCE_S for c in outline.chapters)
            ):
                lecture_id = outline.lecture_id
            citations[label] = Citation(
                label=label,
                at_s=at_s,
                lecture_id=lecture_id,
                segment_id=passage.segment_id if passage else None,
                valid=lecture_id is not None,
            )
    return list(citations.values())
