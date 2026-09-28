"""Golden Q&A sets (evals/datasets/golden-qa): questions about one lecture, each with the
stretches of video that answer it.

Spans are written as "MM:SS" so they're easy to check against the video by hand. Questions with
no spans are ones the lecture doesn't answer; a good answer says so.
"""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, field_validator, model_validator

from lecture_core.notes import parse_timestamp


class GoldenLecture(BaseModel):
    title: str
    video: str
    video_sha256: str
    duration: str
    source: str
    licence: str
    attribution: str

    @property
    def duration_s(self) -> float:
        return parse_timestamp(self.duration)


class Span(BaseModel):
    start_s: float
    end_s: float

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


class GoldenQuestion(BaseModel):
    id: str
    kind: str
    question: str
    answer: str | None
    spans: list[Span]

    @field_validator("spans", mode="before")
    @classmethod
    def _parse_spans(cls, value: object) -> object:
        # [["MM:SS", "MM:SS"], ...] in the file.
        if isinstance(value, list):
            return [
                {"start_s": _seconds(pair[0]), "end_s": _seconds(pair[1])}
                if isinstance(pair, list | tuple) and len(pair) == 2
                else pair
                for pair in value
            ]
        return value

    @property
    def answerable(self) -> bool:
        return bool(self.spans)


class GoldenSet(BaseModel):
    lecture: GoldenLecture
    status: str
    questions: list[GoldenQuestion]

    @classmethod
    def load(cls, path: Path) -> "GoldenSet":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique")
        duration_s = self.lecture.duration_s
        for question in self.questions:
            if (question.answer is None) == question.answerable:
                raise ValueError(f"{question.id}: give an answer exactly when there are spans")
            for span in question.spans:
                if not 0 <= span.start_s < span.end_s <= duration_s:
                    raise ValueError(f"{question.id}: span {span} isn't inside the video")
        return self


def _seconds(value: object) -> object:
    return parse_timestamp(value) if isinstance(value, str) else value
