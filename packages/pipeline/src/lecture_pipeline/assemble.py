"""Turn the LLM steps' segment-cited output into StudyNotes with times.

Concepts get a word-level time: the moment the lecturer first says the term inside the cited
segment, found in the ASR word timestamps. Other items point at the start of their segment.
"""

import re
from collections.abc import Sequence

from pydantic import BaseModel

from lecture_core.notes import Chapter, Concept, Formula, QuizQuestion, StudyNotes
from lecture_core.timeline import Timeline, TimelineSegment, Word
from lecture_llm.agents import ChapterNotes, Overview, PlannedChapter

_STOPWORDS = {"a", "an", "the", "of", "for", "in", "on", "to", "and", "or", "with", "by"}


class ChapterRange(BaseModel):
    title: str
    first: int
    last: int


def chapter_ranges(timeline: Timeline, planned: Sequence[PlannedChapter]) -> list[ChapterRange]:
    """Validate the model's plan: known ids only, in order, starting at the first segment."""
    index = {segment.id: i for i, segment in enumerate(timeline.segments)}
    starts: list[tuple[int, str]] = []
    for chapter in planned:
        position = index.get(chapter.first_segment)
        if position is not None and (not starts or position > starts[-1][0]):
            starts.append((position, chapter.title))
    if not starts:
        starts = [(0, planned[0].title if planned else "Lecture")]
    if starts[0][0] != 0:
        starts[0] = (0, starts[0][1])
    return [
        ChapterRange(
            title=title,
            first=first,
            last=(starts[n + 1][0] - 1) if n + 1 < len(starts) else len(timeline.segments) - 1,
        )
        for n, (first, title) in enumerate(starts)
    ]


def assemble(
    timeline: Timeline,
    chapters: Sequence[tuple[ChapterRange, ChapterNotes]],
    overview: Overview,
) -> tuple[StudyNotes, list[str]]:
    """Returns the notes and a list of items dropped for citing unknown segments."""
    by_id = {segment.id: segment for segment in timeline.segments}
    dropped: list[str] = []

    def segment(item: str, segment_id: str) -> TimelineSegment | None:
        found = by_id.get(segment_id)
        if found is None:
            dropped.append(f"{item}: unknown segment {segment_id!r}")
        return found

    notes_chapters: list[Chapter] = []
    concepts: list[Concept] = []
    formulas: list[Formula] = []
    for n, (span, notes) in enumerate(chapters):
        first, last = timeline.segments[span.first], timeline.segments[span.last]
        next_start = (
            timeline.segments[chapters[n + 1][0].first].start_s
            if n + 1 < len(chapters)
            else max(last.end_s, timeline.duration_s)
        )
        notes_chapters.append(
            Chapter(
                title=span.title, start_s=first.start_s, end_s=next_start, summary=notes.summary
            )
        )
        for c in notes.concepts:
            if (found := segment(f"concept {c.term!r}", c.segment)) is not None:
                at = locate(found.words, c.term) or found.start_s
                concepts.append(Concept(term=c.term, definition=c.definition, at_s=at))
        for f in notes.formulas:
            if (found := segment(f"formula {f.latex!r}", f.segment)) is not None:
                formulas.append(Formula(latex=f.latex, meaning=f.meaning, at_s=found.start_s))

    quiz = [
        QuizQuestion(question=q.question, answer=q.answer, at_s=found.start_s)
        for q in overview.quiz
        if (found := segment(f"quiz {q.question[:40]!r}", q.segment)) is not None
    ]
    study_notes = StudyNotes(
        tldr=overview.tldr,
        chapters=notes_chapters,
        concepts=merge_duplicate_concepts(concepts),
        formulas=formulas,
        quiz=quiz,
    )
    return study_notes, dropped


def merge_duplicate_concepts(concepts: Sequence[Concept]) -> list[Concept]:
    """Notes are written chapter by chapter, so a concept the introduction mentions can come
    back from every chapter that explains it. Keep one per term: the fullest definition, with
    its own citation, since that's where the lecture explains the idea rather than names it."""
    best: dict[str, Concept] = {}
    for concept in concepts:
        key = " ".join(_singular(token) for token in _tokens(concept.term))
        current = best.get(key)
        if current is None or len(concept.definition) > len(current.definition):
            best[key] = concept
    return sorted(best.values(), key=lambda concept: concept.at_s)


def locate(words: Sequence[Word], phrase: str) -> float | None:
    """Start time of the earliest place `phrase` is spoken, matching as many of its leading
    words as possible (at least one that isn't a stopword)."""
    tokens = _tokens(phrase)
    while tokens and tokens[0] in _STOPWORDS:
        tokens.pop(0)
    if not tokens:
        return None
    spoken = [_tokens(word.text) for word in words]
    flat = [
        (token, word.start_s)
        for token_list, word in zip(spoken, words, strict=True)
        for token in token_list
    ]
    best: tuple[int, float] | None = None
    for i, (token, start_s) in enumerate(flat):
        if token != tokens[0]:
            continue
        matched = 1
        while (
            matched < len(tokens)
            and i + matched < len(flat)
            and flat[i + matched][0] == tokens[matched]
        ):
            matched += 1
        if best is None or matched > best[0]:
            best = (matched, start_s)
        if matched == len(tokens):
            break
    return best[1] if best else None


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _singular(token: str) -> str:
    """Rough English singular, enough to match "orders of growth" with "order of growth"."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token
