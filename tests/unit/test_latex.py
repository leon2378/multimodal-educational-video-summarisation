"""LaTeX a model escaped twice is repaired, and LaTeX it wrote into plain text made readable,
wherever either is read (lecture_core.latex)."""

import pytest

from lecture_core.latex import (
    TextPart,
    place_formulas,
    plain,
    undouble_backslashes,
    upright_names,
)
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


@pytest.mark.parametrize(
    ("tex", "upright"),
    [
        # From slide readings of MIT 6.0001 Lectures 10 and 11.
        ("O(len(L))", r"O(\operatorname{len}(L))"),
        (r"n \text{ is } len(L)", r"n \text{ is } \operatorname{len}(L)"),
        ("fib(n-1) + fib(n-2)", r"\operatorname{fib}(n-1) + \operatorname{fib}(n-2)"),
        ("L.append(e)", r"L.\operatorname{append}(e)"),
        # a times T; commands; text; names already upright.
        ("T(n) = aT(n/b) + f(n)", "T(n) = aT(n/b) + f(n)"),
        (r"O(\log n) + \sin(x)", r"O(\log n) + \sin(x)"),
        (r"\text{if len(L) > 0}", r"\text{if len(L) > 0}"),
        (r"\operatorname{len}(L)", r"\operatorname{len}(L)"),
    ],
)
def test_function_names_in_a_formula_are_upright(tex: str, upright: str) -> None:
    assert upright_names(tex) == upright


def test_notes_and_slides_are_repaired_when_read() -> None:
    # As the API reads stored notes and slide readings: those stored before the repair too.
    formula = Formula.model_validate({"latex": DOUBLED, "meaning": "Greedy policy", "at_s": 2421})
    reading = SlideReading.model_validate(
        {
            "slide_id": 7,
            "title": "Q-learning",
            "text": "",
            "figure_description": "",
            "latex": [DOUBLED, r"\gamma", "O(len(L))"],
            "code": "",
        }
    )

    assert formula.latex == REPAIRED
    assert reading.latex == [REPAIRED, r"\gamma", r"O(\operatorname{len}(L))"]


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


def test_a_field_filled_with_a_word_for_nothing_is_empty() -> None:
    # MIT 6.0001 Lecture 11: the prompt said to use "" for nothing, and the model wrote that, or
    # None when it said to use an empty string.
    reading = SlideReading.model_validate(
        {
            "slide_id": 13,
            "title": "LOGARITHMIC COMPLEXITY",
            "text": "- only have to look at loop",
            "figure_description": '""',
            "latex": [],
            "code": " '' ",
        }
    )
    other = reading.model_copy(update={"figure_description": "None"})

    assert (reading.figure_description, reading.code) == ("", "")
    assert SlideReading.model_validate(other.model_dump()).figure_description == ""


@pytest.mark.parametrize("reader", ["ocr", "vlm"])
def test_ocr_reading_big_o_as_zero_is_put_right(reader: str) -> None:
    # MIT 6.0001 Lecture 11, slide 30, read by OCR alone.
    reading = SlideReading.model_validate(
        {
            "slide_id": 30,
            "title": "COMPLEXITY OF COMMON PYTHON FUNCTIONS",
            "text": "index 0(1)\nsort 0(n log n)\nlen 0(len(L))\nf(0), 10(1) and 2.0(3)",
            "figure_description": "",
            "latex": [],
            "code": "",
            "reader": reader,
        }
    )

    if reader == "ocr":
        assert reading.text == "index O(1)\nsort O(n log n)\nlen O(len(L))\nf(0), 10(1) and 2.0(3)"
    else:  # the vision model reads an O as an O
        assert reading.text.startswith("index 0(1)")


def test_formulas_are_placed_where_the_text_writes_them_out() -> None:
    # MIT 6.0001 Lecture 11, as the vision model read it: each formula in the text and listed.
    text = "- Best case:\nO(1)\n- Worst case:\nO(1) + O(n) + O(1) -> O(n)"

    parts, elsewhere = place_formulas(text, ["O(1)", "O(n)"])

    # Formulas with only an operator between them are one.
    assert parts == [
        TextPart("- Best case:\n"),
        TextPart("O(1)", math=True),
        TextPart("\n- Worst case:\n"),
        TextPart(r"O(1) + O(n) + O(1) \to O(n)", math=True),
    ]
    assert elsewhere == []


def test_formulas_the_text_doesnt_write_out_are_kept_apart() -> None:
    # Stanford CS234 Lecture 2: the text has the letter gamma where the formulas have \gamma.
    text = "- γ = 0: Only care about immediate reward\n- If episodes are finite, use γ = 1"

    parts, elsewhere = place_formulas(text, [r"\gamma = 0", r"\gamma < 1", r"\gamma = 1"])

    assert parts == [
        TextPart("- "),
        TextPart(r"\gamma = 0", math=True),
        TextPart(": Only care about immediate reward\n- If episodes are finite, use "),
        TextPart(r"\gamma = 1", math=True),
    ]
    assert elsewhere == [r"\gamma < 1"]


@pytest.mark.parametrize(
    ("text", "formula"),
    [
        ("so i = log n", r"i = \log n"),
        ("if a<=b, stop", r"a \le b"),
        ("a = 2^{n-1} + ... + 2 + 1", r"a = 2^{n-1} + \dots + 2 + 1"),
        ("at base + 4*i", r"base + 4 \cdot i"),
    ],
)
def test_a_formula_is_found_however_its_spelled(text: str, formula: str) -> None:
    parts, elsewhere = place_formulas(text, [formula])

    assert TextPart(formula, math=True) in parts
    assert elsewhere == []


def test_a_formula_is_placed_only_as_a_whole() -> None:
    # MIT 6.0001 Lecture 10. Words between two formulas keep them apart.
    text = "- O(len(L)) for the loop * O(1) to test\n  - O(1 + 4n + 1) = O(4n + 2) = O(n)\nkn^2"
    formulas = ["O(len(L))", "O(1)", "O(n)", "O(1 + 4n + 1) = O(4n + 2) = O(n)", "n^2", "n"]

    parts, elsewhere = place_formulas(text, formulas)

    assert [part.value for part in parts if part.math] == [
        "O(len(L))",
        "O(1)",
        "O(1 + 4n + 1) = O(4n + 2) = O(n)",
    ]
    # O(n) is written out within a longer formula. n^2 is only part of kn^2, and a formula of
    # one letter could be any word.
    assert elsewhere == ["n^2", "n"]
    assert "".join(part.value for part in parts if not part.math).endswith("\nkn^2")
