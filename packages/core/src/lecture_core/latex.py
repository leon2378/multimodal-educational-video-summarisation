"""LaTeX that the models write, repaired where a model escaped it twice, and made readable where
it ended up in plain text.

Reading a slide, the vision model sometimes writes every backslash twice: `\\\\frac{a}{b}` for
`\\frac{a}{b}`. KaTeX reads each pair as a line break, so the command prints as plain letters
("phileftarrowphi") or, inside an environment, the whole formula fails, and the notes copy
whatever the slide reading says. It also writes LaTeX into a slide's text (`\\gamma = 0`, `s \\in
S`), which is shown as it is. `LaTeX` repairs a formula, and sets the names of functions it
calls upright (len, not l, e and n in italics); `PlainText` makes such text readable (γ = 0,
s ∈ S). Both work wherever one is read, from the model, the stage cache or the database, so
lectures read before this are repaired too.

A slide reading lists the slide's formulas and often writes them out in its text as well;
`place_formulas` finds them there, so a slide can be shown with its formulas in place, once.
"""

import re
from collections.abc import Sequence
from typing import Annotated, NamedTuple

from pydantic import AfterValidator

_BACKSLASHES = re.compile(r"\\+")
# Two backslashes and a command name: in a formula escaped once, a line break and then letters.
_DOUBLED_COMMAND = re.compile(r"\\\\[A-Za-z]{2,}")


def undouble_backslashes(tex: str) -> str:
    """`tex` with each pair of backslashes made one again, if it was escaped twice: every
    backslash in it comes in pairs, and a pair starts a command. A formula that has a line break
    (`\\\\`) among single-backslash commands is left as it is, and so is one with nothing but
    line breaks."""
    runs = _BACKSLASHES.findall(tex)
    if not runs or any(len(run) % 2 for run in runs) or not _DOUBLED_COMMAND.search(tex):
        return tex
    return _BACKSLASHES.sub(lambda run: "\\" * (len(run.group()) // 2), tex)


# A lowercase name called like a function: len(L), fib(n), L.append(e). Not aT(n/b), which is a
# times T.
_FUNCTION_NAME = re.compile(r"(?<![\\A-Za-z])([a-z]{2,})(?=\()")
# Text inside a formula, where a math command would break it.
_TEXT_GROUP = re.compile(r"\\(?:text\w*|math(?:rm|sf|tt|it|bf)|operatorname\*?|mbox)\s*\{[^{}]*\}")


def upright_names(tex: str) -> str:
    """`tex` with the names of functions it calls set upright, as \\log is: KaTeX shows len as
    the variables l, e and n, in italics."""
    parts: list[str] = []
    done = 0
    for group in _TEXT_GROUP.finditer(tex):
        parts += [_FUNCTION_NAME.sub(r"\\operatorname{\1}", tex[done : group.start()]), group[0]]
        done = group.end()
    parts.append(_FUNCTION_NAME.sub(r"\\operatorname{\1}", tex[done:]))
    return "".join(parts)


def unique(formulas: list[str]) -> list[str]:
    """Each formula once, in order: a slide reading sometimes lists one twice."""
    return list(dict.fromkeys(formulas))


# Commands that stand for a character, written as that character in plain text.
_SYMBOLS = {
    "alpha": "\N{GREEK SMALL LETTER ALPHA}",
    "beta": "\N{GREEK SMALL LETTER BETA}",
    "gamma": "\N{GREEK SMALL LETTER GAMMA}",
    "delta": "\N{GREEK SMALL LETTER DELTA}",
    "epsilon": "\N{GREEK SMALL LETTER EPSILON}",
    "varepsilon": "\N{GREEK SMALL LETTER EPSILON}",
    "zeta": "\N{GREEK SMALL LETTER ZETA}",
    "eta": "\N{GREEK SMALL LETTER ETA}",
    "theta": "\N{GREEK SMALL LETTER THETA}",
    "iota": "\N{GREEK SMALL LETTER IOTA}",
    "kappa": "\N{GREEK SMALL LETTER KAPPA}",
    "lambda": "\N{GREEK SMALL LETTER LAMDA}",
    "mu": "\N{GREEK SMALL LETTER MU}",
    "nu": "\N{GREEK SMALL LETTER NU}",
    "xi": "\N{GREEK SMALL LETTER XI}",
    "pi": "\N{GREEK SMALL LETTER PI}",
    "rho": "\N{GREEK SMALL LETTER RHO}",
    "sigma": "\N{GREEK SMALL LETTER SIGMA}",
    "tau": "\N{GREEK SMALL LETTER TAU}",
    "upsilon": "\N{GREEK SMALL LETTER UPSILON}",
    "phi": "\N{GREEK SMALL LETTER PHI}",
    "varphi": "\N{GREEK SMALL LETTER PHI}",
    "chi": "\N{GREEK SMALL LETTER CHI}",
    "psi": "\N{GREEK SMALL LETTER PSI}",
    "omega": "\N{GREEK SMALL LETTER OMEGA}",
    "Gamma": "\N{GREEK CAPITAL LETTER GAMMA}",
    "Delta": "\N{GREEK CAPITAL LETTER DELTA}",
    "Theta": "\N{GREEK CAPITAL LETTER THETA}",
    "Lambda": "\N{GREEK CAPITAL LETTER LAMDA}",
    "Pi": "\N{GREEK CAPITAL LETTER PI}",
    "Sigma": "\N{GREEK CAPITAL LETTER SIGMA}",
    "Phi": "\N{GREEK CAPITAL LETTER PHI}",
    "Psi": "\N{GREEK CAPITAL LETTER PSI}",
    "Omega": "\N{GREEK CAPITAL LETTER OMEGA}",
    "in": "\N{ELEMENT OF}",
    "notin": "\N{NOT AN ELEMENT OF}",
    "subset": "\N{SUBSET OF}",
    "subseteq": "\N{SUBSET OF OR EQUAL TO}",
    "cup": "\N{UNION}",
    "cap": "\N{INTERSECTION}",
    "emptyset": "\N{EMPTY SET}",
    "le": "\N{LESS-THAN OR EQUAL TO}",
    "leq": "\N{LESS-THAN OR EQUAL TO}",
    "ge": "\N{GREATER-THAN OR EQUAL TO}",
    "geq": "\N{GREATER-THAN OR EQUAL TO}",
    "neq": "\N{NOT EQUAL TO}",
    "ne": "\N{NOT EQUAL TO}",
    "approx": "\N{ALMOST EQUAL TO}",
    "sim": "\N{TILDE OPERATOR}",
    "equiv": "\N{IDENTICAL TO}",
    "propto": "\N{PROPORTIONAL TO}",
    "times": "\N{MULTIPLICATION SIGN}",
    "cdot": "\N{MIDDLE DOT}",
    "pm": "\N{PLUS-MINUS SIGN}",
    "infty": "\N{INFINITY}",
    "partial": "\N{PARTIAL DIFFERENTIAL}",
    "nabla": "\N{NABLA}",
    "sum": "\N{N-ARY SUMMATION}",
    "prod": "\N{N-ARY PRODUCT}",
    "int": "\N{INTEGRAL}",
    "forall": "\N{FOR ALL}",
    "exists": "\N{THERE EXISTS}",
    "neg": "\N{NOT SIGN}",
    "to": "\N{RIGHTWARDS ARROW}",
    "rightarrow": "\N{RIGHTWARDS ARROW}",
    "leftarrow": "\N{LEFTWARDS ARROW}",
    "gets": "\N{LEFTWARDS ARROW}",
    "leftrightarrow": "\N{LEFT RIGHT ARROW}",
    "Rightarrow": "\N{RIGHTWARDS DOUBLE ARROW}",
    "implies": "\N{RIGHTWARDS DOUBLE ARROW}",
    "Leftrightarrow": "\N{LEFT RIGHT DOUBLE ARROW}",
    "iff": "\N{LEFT RIGHT DOUBLE ARROW}",
    "dots": "\N{HORIZONTAL ELLIPSIS}",
    "ldots": "\N{HORIZONTAL ELLIPSIS}",
    "cdots": "\N{MIDLINE HORIZONTAL ELLIPSIS}",
    "vdots": "\N{VERTICAL ELLIPSIS}",
    "ell": "\N{SCRIPT SMALL L}",
    "prime": "\N{PRIME}",
    "mid": "|",
    "quad": " ",
    "qquad": " ",
    # Operator names, which are written out.
    **{name: name for name in ("arg", "max", "min", "log", "ln", "exp", "lim", "sin", "cos")},
}
# Commands whose argument is shown with a mark over it.
_ACCENTS = {
    "hat": "\N{COMBINING CIRCUMFLEX ACCENT}",
    "widehat": "\N{COMBINING CIRCUMFLEX ACCENT}",
    "bar": "\N{COMBINING MACRON}",
    "overline": "\N{COMBINING OVERLINE}",
    "tilde": "\N{COMBINING TILDE}",
    "widetilde": "\N{COMBINING TILDE}",
    "vec": "\N{COMBINING RIGHT ARROW ABOVE}",
    "dot": "\N{COMBINING DOT ABOVE}",
}
# Commands that only change the font: their argument as it is.
_FONTS = {
    "mathbf",
    "mathrm",
    "mathit",
    "mathsf",
    "mathtt",
    "boldsymbol",
    "bm",
    "text",
    "textbf",
    "textit",
    "textrm",
    "operatorname",
    "mbox",
    "emph",
    "mathcal",
    "mathscr",
}
_BLACKBOARD = {
    "E": "\N{MATHEMATICAL DOUBLE-STRUCK CAPITAL E}",
    "R": "\N{DOUBLE-STRUCK CAPITAL R}",
    "N": "\N{DOUBLE-STRUCK CAPITAL N}",
    "Z": "\N{DOUBLE-STRUCK CAPITAL Z}",
    "Q": "\N{DOUBLE-STRUCK CAPITAL Q}",
    "C": "\N{DOUBLE-STRUCK CAPITAL C}",
    "P": "\N{DOUBLE-STRUCK CAPITAL P}",
}
_WITH_ARGUMENT = re.compile(r"\\([A-Za-z]+)\s*\{([^{}]*)\}")
_FRACTION = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_ENVIRONMENT = re.compile(r"\\(?:begin|end)\s*\{[A-Za-z*]*\}")
_ESCAPED = {"{": "{", "}": "}", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "\\": " "}
_SPACING = re.compile(r"\\[,;:! ]")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
# A subscript or superscript of one character needs no braces: π_{θ} reads as π_θ.
_ONE_CHARACTER_GROUP = re.compile(r"([_^])\{(\S)\}")
_SPACES = re.compile(r"(?<=\S)[ \t]{2,}")


def plain(text: str) -> str:
    """`text` with the LaTeX a model wrote into it made readable as plain text: Greek letters and
    symbols as their characters, accents as marks over their letter, font commands as their
    argument, fractions as a/b, escaped characters as themselves. A command it doesn't know is
    left as it is, and text without a backslash is returned unchanged, so `%`, `$` and braces
    in ordinary text are safe."""
    if "\\" not in text:
        return text
    text = _ENVIRONMENT.sub("", undouble_backslashes(text))
    # Innermost first, until nothing changes: \frac{dQ_{\phi}}{d\phi} takes a few passes.
    for _ in range(10):
        before = text
        text = _COMMAND.sub(_symbol, text)
        text = _ONE_CHARACTER_GROUP.sub(r"\1\2", text)
        text = _FRACTION.sub(lambda m: f"{_grouped(m.group(1))}/{_grouped(m.group(2))}", text)
        text = _WITH_ARGUMENT.sub(_argument, text)
        if text == before:
            break
    text = re.sub(r"\\([{}%$&#_\\])", lambda m: _ESCAPED[m.group(1)], text)
    text = _SPACING.sub(" ", text)
    # Spaces left where commands went, but not the indent of a nested bullet.
    return "\n".join(_SPACES.sub(" ", line).rstrip() for line in text.split("\n"))


def _symbol(match: re.Match[str]) -> str:
    return _SYMBOLS.get(match.group(1), match.group(0))


def _argument(match: re.Match[str]) -> str:
    command, argument = match.group(1), match.group(2)
    if command in _ACCENTS and argument:
        return argument + _ACCENTS[command]
    if command == "mathbb":
        return "".join(_BLACKBOARD.get(letter, letter) for letter in argument)
    if command in _FONTS:
        return argument
    if command == "sqrt":
        return "\N{SQUARE ROOT}" + _grouped(argument)
    return match.group(0)


def _grouped(text: str) -> str:
    return text if len(text) <= 1 else f"({text})"


class TextPart(NamedTuple):
    """Part of a text: plain text, or a formula written out in it (`math`), as LaTeX."""

    value: str
    math: bool = False


def place_formulas(text: str, formulas: Sequence[str]) -> tuple[list[TextPart], list[str]]:
    """`text` in parts, with each of `formulas` that it writes out put in its place as LaTeX;
    and the formulas it doesn't write out. A formula is found however it's spelled (`\\gamma =
    1` as γ = 1, `\\le` as <=, spaced differently), but only as a whole: not n in "len" or in
    t_n, nor O(n) in O(n log n), as the longest formulas are placed first. Formulas with just an
    operator between them become one: O(1) + O(n) -> O(n)."""
    compared, origins = _compared(text)
    placed: list[tuple[int, int, str]] = []
    found: set[str] = set()
    for formula in sorted(formulas, key=lambda f: len(_compared(plain(f))[0]), reverse=True):
        wanted = _compared(plain(formula))[0]
        # One letter could be any word: "a".
        at = compared.find(wanted) if len(wanted) > 1 else -1
        while at >= 0:
            start, end = origins[at][0], origins[at + len(wanted) - 1][1]
            if _whole(text, start, end):
                if not any(s < end and start < e for s, e, _ in placed):
                    placed.append((start, end, formula))
                    found.add(formula)
                elif any(s <= start and end <= e for s, e, _ in placed):
                    found.add(formula)  # written out as part of a longer one
            at = compared.find(wanted, at + 1)

    parts: list[TextPart] = []
    done = 0
    for start, end, formula in sorted(placed):
        if start > done:
            parts.append(TextPart(text[done:start]))
        parts.append(TextPart(formula, math=True))
        done = end
    if done < len(text):
        parts.append(TextPart(text[done:]))

    joined: list[TextPart] = []
    for part in parts:
        if part.math and len(joined) > 1 and joined[-2].math and not joined[-1].math:
            operator = _OPERATORS.get(joined[-1].value.strip(" \t"))
            if operator is not None:
                joined.pop()
                part = TextPart(f"{joined.pop().value} {operator} {part.value}", math=True)
        joined.append(part)
    return joined, [formula for formula in formulas if formula not in found]


# A formula and the text it's looked for in are compared without spaces, and with symbols spelled
# one way: a model writes -> or →, <= or ≤.
_SPELLED = {
    "->": _SYMBOLS["to"],
    "<-": _SYMBOLS["gets"],
    "=>": _SYMBOLS["implies"],
    "<=": _SYMBOLS["le"],
    ">=": _SYMBOLS["ge"],
    "!=": _SYMBOLS["ne"],
    "...": _SYMBOLS["dots"],
    _SYMBOLS["cdots"]: _SYMBOLS["dots"],
    "*": _SYMBOLS["cdot"],
}
_TOKEN = re.compile(r"\.\.\.|->|<-|=>|<=|>=|!=|[ \t]+|.", re.DOTALL)
# What joins two formulas on a line into one, and how it's written in LaTeX.
_OPERATORS = {
    **{operator: operator for operator in "+-=<>/*"},
    "->": r"\to",
    "<-": r"\gets",
    "=>": r"\Rightarrow",
    "<=": r"\le",
    ">=": r"\ge",
    "!=": r"\ne",
    **{_SYMBOLS[name]: f"\\{name}" for name in ("to", "gets", "le", "ge", "ne", "times", "cdot")},
    _SYMBOLS["implies"]: r"\Rightarrow",
}


def _compared(text: str) -> tuple[str, list[tuple[int, int]]]:
    """`text` as it's compared, and where in `text` each of its characters came from."""
    characters: list[str] = []
    origins: list[tuple[int, int]] = []
    for token in _TOKEN.finditer(text):
        if token.group().strip(" \t"):
            characters.append(_SPELLED.get(token.group(), token.group()))
            origins.append(token.span())
    return "".join(characters), origins


def _whole(text: str, start: int, end: int) -> bool:
    """Whether `text[start:end]` stands on its own, rather than being part of a longer word or
    expression: n in "len", "t_n" or "n^2"."""
    before = text[start - 1] if start else " "
    after = text[end] if end < len(text) else " "
    return not (_joined(before) and _joined(text[start])) and not (
        _joined(text[end - 1]) and _joined(after)
    )


def _joined(character: str) -> bool:
    return character.isalnum() or character in "_^'"


# A formula from a model, repaired when it's read.
LaTeX = Annotated[str, AfterValidator(undouble_backslashes), AfterValidator(upright_names)]
# A slide's formulas, each repaired and listed once.
Formulas = Annotated[list[LaTeX], AfterValidator(unique)]
# Text from a model, with any LaTeX in it made readable.
PlainText = Annotated[str, AfterValidator(plain)]
