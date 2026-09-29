"""Human captions (SRT) as ground truth: for word error rate, and for where a term is said.

Words are normalised the same way on both sides before comparing: lower case, speaker labels
and bracketed notes like [LAUGHTER] removed, hyphens split, other punctuation dropped.
"""

import re
from collections.abc import Sequence

from pydantic import BaseModel

_CUE_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")
# "PROFESSOR:", "AUDIENCE MEMBER:". Two or more capitals, so "big O: ..." isn't a speaker.
_SPEAKER = re.compile(r"(?:^|\s)[A-Z]{2,}(?:\s[A-Z]{2,})*:\s")
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([A-Z][A-Z ]*\)")
_NOT_WORD = re.compile(r"[^\w\s']")
_APOSTROPHES = {0x2018: "'", 0x2019: "'"}


class Cue(BaseModel):
    start_s: float
    end_s: float
    text: str


class TimedWord(BaseModel):
    at_s: float
    word: str


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            match = _CUE_TIME.search(line)
            if match:
                h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
                start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
                end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
                cues.append(Cue(start_s=start, end_s=end, text=" ".join(lines[i + 1 :])))
                break
    return cues


def normalise(text: str) -> list[str]:
    text = _ANNOTATION.sub(" ", _SPEAKER.sub(" ", f" {text}"))
    # Curly apostrophes count as straight ones.
    text = text.translate(_APOSTROPHES).replace("-", " ").lower()
    words = _NOT_WORD.sub(" ", text).split()
    return [w.strip("'") for w in words if w.strip("'")]


def timed_words(cues: Sequence[Cue]) -> list[TimedWord]:
    """Every word with an estimated time: a cue's words spread evenly over it."""
    words = []
    for cue in cues:
        cue_words = normalise(cue.text)
        step = (cue.end_s - cue.start_s) / max(len(cue_words), 1)
        words += [
            TimedWord(at_s=cue.start_s + i * step, word=word) for i, word in enumerate(cue_words)
        ]
    return words


def _stem(word: str) -> str:
    # Enough to match "orders of growth" with "order of growth".
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def occurrences(term: str, words: Sequence[TimedWord]) -> list[float]:
    """The times at which `term` (one or more words) is said."""
    wanted = [_stem(w) for w in normalise(term)]
    if not wanted:
        return []
    stems = [_stem(w.word) for w in words]
    return [
        words[i].at_s
        for i in range(len(stems) - len(wanted) + 1)
        if stems[i : i + len(wanted)] == wanted
    ]


def count(term: str, words: Sequence[str]) -> int:
    """How many times `term` appears in normalised `words`."""
    wanted = [_stem(w) for w in normalise(term)]
    stems = [_stem(w) for w in words]
    if not wanted:
        return 0
    return sum(stems[i : i + len(wanted)] == wanted for i in range(len(stems) - len(wanted) + 1))
