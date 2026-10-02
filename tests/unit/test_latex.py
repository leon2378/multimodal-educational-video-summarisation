"""LaTeX a model escaped twice is repaired, and LaTeX it wrote into plain text made readable,
wherever either is read (lecture_core.latex)."""

import pytest

from lecture_core.latex import plain, undouble_backslashes
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


@pytest.mark.parametrize(
    ("text", "readable"),
    [
        # From slide readings of the two test lectures.
        (
            r"- \gamma = 0: Only care about immediate reward",
            "- γ = 0: Only care about immediate reward",
        ),
        (r"S is a (finite) set of states (s \in S)", "S is a (finite) set of states (s ∈ S)"),
        (r"S_4, S_5, S_6, \dots", "S_4, S_5, S_6, …"),
        (r"Sample \bar{a}'_i \sim \pi_{\theta}(\cdot|s'_i)", "Sample a\u0304'_i ∼ π_θ(·|s'_i)"),
        (r"an estimate \hat{Q}^{\pi}(s, a)", "an estimate Q\u0302^π(s, a)"),
        (r"a batch \{s_i, a_i, r_i, s'_i\} from R", "a batch {s_i, a_i, r_i, s'_i} from R"),
        (r"a_t = \arg\max_{\mathbf{a}} Q_{\phi}(s_t, a)", "a_t = argmax_a Q_φ(s_t, a)"),
        (r"R(s) = \mathbb{E}[r_t | s_t = s]", "R(s) = 𝔼[r_t | s_t = s]"),
        (r"\alpha \sum_i \frac{dQ_{\phi}}{d\phi}", "α ∑_i (dQ_φ)/(dφ)"),
        (r"1 & \text{if } x \\ 0 & \text{otherwise}", "1 & if x 0 & otherwise"),
        # Escaped twice, as a formula can be.
        ("\\\\gamma = 1", "γ = 1"),
    ],
)
def test_latex_in_plain_text_is_made_readable(text: str, readable: str) -> None:
    assert plain(text) == readable


@pytest.mark.parametrize(
    "text",
    [
        "Costs 50% of $10 & more {x}",
        r"C:\Users\me",
        "  - a nested bullet, indented",
        "",
    ],
)
def test_text_without_latex_is_left_alone(text: str) -> None:
    assert plain(text) == text


def test_a_slide_reading_is_tidied_when_read() -> None:
    reading = SlideReading.model_validate(
        {
            "slide_id": 12,
            "title": "Discount Factor",
            "text": "Discount Factor\n- \\gamma = 0: Only care about immediate reward",
            "figure_description": "",
            "latex": [r"\gamma = 0", r"\gamma = 1", r"\gamma < 1", r"\gamma = 1"],
            "code": "",
        }
    )

    # The title isn't repeated, the text reads as text, and each formula is listed once.
    assert reading.text == "- γ = 0: Only care about immediate reward"
    assert reading.latex == [r"\gamma = 0", r"\gamma = 1", r"\gamma < 1"]
