"""Stand-ins for the speech model, the LLM and the search models, so tests run offline."""

import math
import re
import zlib
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from lecture_core.timeline import Transcript, TranscriptSegment, Word
from lecture_rag.encoders import SparseVector


def _segment(start: float, text: str) -> TranscriptSegment:
    words = []
    for i, token in enumerate(text.split()):
        words.append(
            Word(start_s=start + i * 0.4, end_s=start + i * 0.4 + 0.3, text=token, probability=0.9)
        )
    return TranscriptSegment(start_s=start, end_s=words[-1].end_s, text=text, words=words)


# Speech for the synthetic lecture: before the first slide, over slide A, over slide B, and
# over slide A again.
TRANSCRIPT = Transcript(
    language="en",
    duration_s=12.0,
    segments=[
        _segment(0.2, "welcome back everyone"),
        _segment(2.2, "today memoisation stores the results of subproblems"),
        _segment(6.2, "big O notation describes growth"),
        _segment(9.2, "so memoisation again"),
    ],
)


class FakeTranscriber:
    model_id = "fake-whisper"
    device = "cpu"

    def __init__(self) -> None:
        self.calls = 0

    def cache_params(self) -> dict[str, Any]:
        return {"beam_size": 1}

    def transcribe(
        self, audio: BinaryIO, on_progress: Callable[[float], None] | None = None
    ) -> Transcript:
        self.calls += 1
        assert audio.read(4) == b"fLaC"
        return TRANSCRIPT

    def close(self) -> None:
        pass


class FakeLLM:
    """Answers each of the four agents by the title of its output schema, citing whatever
    segment ids appear in the prompt."""

    def __init__(self, drop_slide: int | None = None) -> None:
        self.calls: list[str] = []
        self.drop_slide = drop_slide
        self.model = FunctionModel(self._respond, model_name="fake-llm")

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        kind = str(tool.parameters_json_schema.get("title"))
        self.calls.append(kind)
        prompt = " ".join(
            str(part.content) for message in messages for part in getattr(message, "parts", [])
        )
        segments = re.findall(r'id="(s\d+)"', prompt)
        args: dict[str, Any]
        if kind == "_SlideBatch":
            ids = [int(n) for n in re.findall(r"Slide id (\d+):", prompt)]
            args = {
                "slides": [
                    {
                        "slide_id": n,
                        "title": f"Slide {n}",
                        "text": "- a bullet",
                        "figure_description": "",
                        "latex": ["O(n)"] if n == 1 else [],
                        "code": "",
                    }
                    for n in ids
                    if n != self.drop_slide or len(ids) == 1
                ]
            }
        elif kind == "_ChapterPlan":
            args = {
                "chapters": [
                    {"title": "Memoisation", "first_segment": segments[0]},
                    {"title": "Growth", "first_segment": segments[2]},
                ]
            }
        elif kind == "ChapterNotes":
            args = {
                "summary": f"Covers {', '.join(segments)}.",
                "concepts": [
                    {"term": "memoisation", "definition": "Caching.", "segment": segments[-1]},
                    {"term": "ghost", "definition": "Made up.", "segment": "s999"},
                ],
                "formulas": [{"latex": "O(n)", "meaning": "Linear.", "segment": segments[0]}],
            }
        else:
            args = {
                "tldr": "A short lecture.",
                "quiz": [
                    {
                        "question": "What is memoisation?",
                        "answer": "Caching.",
                        "segment": segments[1],
                    }
                ],
            }
        return ModelResponse(parts=[ToolCallPart(tool.name, args)])


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bucket(token: str, size: int) -> int:
    return zlib.crc32(token.encode()) % size


class FakeDense:
    """A hashed bag of words: texts that share words point the same way."""

    model_id = "fake-dense"
    size = 64

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.size
        for token in _tokens(text):
            vector[_bucket(token, self.size)] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class FakeSparse:
    """Word counts by hashed token id; Qdrant adds the IDF weighting."""

    model_id = "fake-bm25"

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> SparseVector:
        counts = Counter(_bucket(token, 1 << 20) for token in _tokens(text))
        return SparseVector(indices=list(counts), values=[float(n) for n in counts.values()])


class FakeReranker:
    """The share of the query's words that appear in each text."""

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        words = set(_tokens(query))
        return [len(words & set(_tokens(text))) / max(len(words), 1) for text in texts]
