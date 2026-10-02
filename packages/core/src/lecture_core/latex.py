"""LaTeX that the models write, repaired where a model escaped it twice.

Reading a slide, the vision model sometimes writes every backslash twice: `\\\\frac{a}{b}` for
`\\frac{a}{b}`. KaTeX reads each pair as a line break, so the command prints as plain letters
("phileftarrowphi") or, inside an environment, the whole formula fails, and the notes copy
whatever the slide reading says. `LaTeX` repairs a formula wherever one is read, from the model,
the stage cache or the database, so lectures read before this are repaired too.
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


# A formula from a model, repaired when it's read.
LaTeX = Annotated[str, AfterValidator(undouble_backslashes)]
