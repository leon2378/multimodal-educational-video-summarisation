"""LaTeX that the models write, repaired where a model escaped it twice, and made readable where
it ended up in plain text.

Reading a slide, the vision model sometimes writes every backslash twice: `\\\\frac{a}{b}` for
`\\frac{a}{b}`. KaTeX reads each pair as a line break, so the command prints as plain letters
("phileftarrowphi") or, inside an environment, the whole formula fails, and the notes copy
whatever the slide reading says. It also writes LaTeX into a slide's text (`\\gamma = 0`, `s \\in
S`), which is shown as it is. `LaTeX` repairs a formula and `PlainText` makes such text
readable (γ = 0, s ∈ S) wherever one is read, from the model, the stage cache or the database,
so lectures read before this are repaired too.
"""

import re
from typing import Annotated

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


# A formula from a model, repaired when it's read.
LaTeX = Annotated[str, AfterValidator(undouble_backslashes)]
# A slide's formulas, each repaired and listed once.
Formulas = Annotated[list[LaTeX], AfterValidator(unique)]
# Text from a model, with any LaTeX in it made readable.
PlainText = Annotated[str, AfterValidator(plain)]
