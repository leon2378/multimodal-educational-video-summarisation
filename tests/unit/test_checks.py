import pytest

from lecture_core.notes import Chapter, Concept, StudyNotes
from lecture_evals.checks import check_notes


def notes(chapters: list[tuple[float, float]], concept_at: float = 30) -> StudyNotes:
    return StudyNotes(
        tldr="",
        chapters=[
            Chapter(title=f"Part {i}", start_s=start, end_s=end, summary="")
            for i, (start, end) in enumerate(chapters)
        ],
        concepts=[Concept(term="memoisation", definition="", at_s=concept_at)],
        formulas=[],
        quiz=[],
    )


def test_clean_notes_have_no_issues() -> None:
    checks = check_notes(notes([(0, 300), (300, 600)]), duration_s=600)
    assert checks.issues == []
    assert checks.timestamps == 5
    assert checks.chapter_coverage == pytest.approx(1.0)


def test_flags_citations_outside_the_video() -> None:
    checks = check_notes(notes([(0, 600)], concept_at=750), duration_s=600)
    assert checks.out_of_range == 1
    assert "concepts[0] at 12:30 is outside the video" in checks.issues[0]


def test_flags_gaps_overlaps_and_backwards_chapters() -> None:
    checks = check_notes(notes([(0, 200), (260, 400), (380, 500), (550, 520)]), duration_s=600)
    assert checks.issues == [
        "chapters[3] ends before it starts (09:10-08:40)",
        "60s gap between chapters[0] and chapters[1]",
        "chapters[1] and chapters[2] overlap by 20s",
        "50s gap between chapters[2] and chapters[3]",
    ]
    # Covered: 0-200, 260-500. Overlap counted once, the backwards chapter not at all.
    assert checks.chapter_coverage == pytest.approx(440 / 600)


def test_unknown_duration_skips_range_checks() -> None:
    checks = check_notes(notes([(0, 600)], concept_at=9999), duration_s=None)
    assert checks.out_of_range == 0
    assert checks.chapter_coverage is None
