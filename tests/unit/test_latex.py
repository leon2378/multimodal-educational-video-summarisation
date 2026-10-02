"""LaTeX a model escaped twice is repaired wherever it's read (lecture_core.latex)."""

import pytest

from lecture_core.latex import undouble_backslashes
from lecture_core.notes import Formula, StudyNotes
from lecture_core.timeline import SlideReading

# From a slide reading of Stanford CS224R Lecture 6, as stored: every backslash doubled.
DOUBLED = (
    r"\\pi(\\mathbf{a}_t|\\mathbf{s}_t) = \\begin{cases} 1 & \\text{if } \\mathbf{a}_t = "
    r"\\arg\\max_{\\mathbf{a}} Q_{\\phi}(\\mathbf{s}_t, \\mathbf{a}) \\\\ 0 & \\text{otherwise} "
    r"\\end{cases}"
)
REPAIRED = (
    r"\pi(\mathbf{a}_t|\mathbf{s}_t) = \begin{cases} 1 & \text{if } \mathbf{a}_t = "
    r"\arg\max_{\mathbf{a}} Q_{\phi}(\mathbf{s}_t, \mathbf{a}) \\ 0 & \text{otherwise} "
    r"\end{cases}"
)


@pytest.mark.parametrize(
    ("tex", "repaired"),
    [
        (DOUBLED, REPAIRED),
        (r"\\phi' \\leftarrow \\phi", r"\phi' \leftarrow \phi"),
        (r"Q_{\\phi}", r"Q_{\phi}"),
    ],
)
def test_a_formula_escaped_twice_is_repaired(tex: str, repaired: str) -> None:
    assert undouble_backslashes(tex) == repaired
    # Repairing again changes nothing.
    assert undouble_backslashes(repaired) == repaired


@pytest.mark.parametrize(
    "tex",
    [
        # Line breaks among single-backslash commands: escaped once, as it should be.
        r"P = \begin{pmatrix} a & b \\ c & d \end{pmatrix}",
        REPAIRED,
        # Nothing but a line break, or no backslash at all.
        r"a \\ b",
        r"O(n^2)",
        "",
    ],
)
def test_a_formula_escaped_once_is_left_alone(tex: str) -> None:
    assert undouble_backslashes(tex) == tex


def test_notes_and_slides_are_repaired_when_read() -> None:
    # As the API reads stored notes and slide readings: those stored before the repair too.
    formula = Formula.model_validate({"latex": DOUBLED, "meaning": "Greedy policy", "at_s": 2421})
    reading = SlideReading.model_validate(
        {
            "slide_id": 7,
            "title": "Q-learning",
            "text": "",
            "figure_description": "",
            "latex": [DOUBLED, r"\gamma"],
            "code": "",
        }
    )

    assert formula.latex == REPAIRED
    assert reading.latex == [REPAIRED, r"\gamma"]


def test_a_formula_shown_again_is_listed_once() -> None:
    notes = StudyNotes.model_validate(
        {
            "tldr": "",
            "chapters": [],
            "concepts": [],
            "quiz": [],
            "formulas": [
                {"latex": REPAIRED, "meaning": "Greedy policy", "at_s": 2035},
                {"latex": r"\gamma", "meaning": "Discount", "at_s": 2100},
                # The recap slide, read with every backslash doubled.
                {"latex": DOUBLED, "meaning": "Greedy policy again", "at_s": 2421},
            ],
        }
    )

    assert [(f.latex, f.at_s) for f in notes.formulas] == [(REPAIRED, 2035), (r"\gamma", 2100)]
