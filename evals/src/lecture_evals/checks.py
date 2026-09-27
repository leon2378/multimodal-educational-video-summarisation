"""Structural checks on study notes, for any producer (pipeline or baseline).

These catch citations that can't be right: past the end of the video, or chapters that run
backwards, overlap or leave gaps. Whether a citation points at the *right* moment needs the
transcript, and is scored by the Phase 4 eval suites.
"""

from itertools import pairwise

from pydantic import BaseModel

from lecture_core.notes import StudyNotes, format_timestamp


class NotesChecks(BaseModel):
    timestamps: int
    out_of_range: int
    # Share of the video covered by chapters. None when the duration is unknown.
    chapter_coverage: float | None
    issues: list[str]


def check_notes(
    notes: StudyNotes, duration_s: float | None, tolerance_s: float = 2.0
) -> NotesChecks:
    issues: list[str] = []
    cited: list[tuple[str, float]] = []

    for i, chapter in enumerate(notes.chapters):
        cited += [(f"chapters[{i}].start", chapter.start_s), (f"chapters[{i}].end", chapter.end_s)]
        if chapter.end_s <= chapter.start_s:
            issues.append(
                f"chapters[{i}] ends before it starts "
                f"({format_timestamp(chapter.start_s)}-{format_timestamp(chapter.end_s)})"
            )
    for i, (before, after) in enumerate(pairwise(notes.chapters)):
        gap = after.start_s - before.end_s
        if gap > tolerance_s:
            issues.append(f"{gap:.0f}s gap between chapters[{i}] and chapters[{i + 1}]")
        elif gap < -tolerance_s:
            issues.append(f"chapters[{i}] and chapters[{i + 1}] overlap by {-gap:.0f}s")

    cited += [(f"concepts[{i}]", c.at_s) for i, c in enumerate(notes.concepts)]
    cited += [(f"formulas[{i}]", f.at_s) for i, f in enumerate(notes.formulas)]
    cited += [(f"quiz[{i}]", q.at_s) for i, q in enumerate(notes.quiz)]

    out_of_range = 0
    coverage = None
    if duration_s is not None and duration_s > 0:
        for label, at_s in cited:
            if at_s < 0 or at_s > duration_s + tolerance_s:
                out_of_range += 1
                issues.append(
                    f"{label} at {format_timestamp(at_s)} is outside the video "
                    f"(length {format_timestamp(duration_s)})"
                )
        coverage = _covered_seconds(notes, duration_s) / duration_s

    return NotesChecks(
        timestamps=len(cited),
        out_of_range=out_of_range,
        chapter_coverage=coverage,
        issues=issues,
    )


def _covered_seconds(notes: StudyNotes, duration_s: float) -> float:
    intervals = sorted(
        (max(c.start_s, 0), min(c.end_s, duration_s)) for c in notes.chapters if c.end_s > c.start_s
    )
    covered, reach = 0.0, 0.0
    for start, end in intervals:
        start = max(start, reach)
        if end > start:
            covered += end - start
            reach = end
    return covered
