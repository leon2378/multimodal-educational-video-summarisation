"""Answer eval: ask the golden questions through the API, then score the answers.

- Correctness and faithfulness: an LLM judge (prompts/evals/judge-answer.v2.md) compares each
  answer with the reference answer, and checks its claims against what it was written from: the
  passages, with their slides, and the lecture's outline. The judge isn't calibrated against
  hand grades yet (the blueprint wants about 50), so read its scores as a trend rather than the
  truth.
- Citations: the share inside the retrieved passages (checked by the API), and the share of
  answers citing within 10 s of where the golden set says the answer is.
- Declining: a question the lecture doesn't answer should be declined, citing nothing; one it
  does answer shouldn't be.
- Latency and tokens: time to first token and in total, and tokens per answer.

Each question gets a thread of its own, deleted afterwards, so evals leave no conversations
behind.
"""

import contextlib
import html
import math
import statistics
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from lecture_core.notes import StudyNotes, format_timestamp
from lecture_core.qa import Outline
from lecture_core.timeline import SlideReading
from lecture_evals.client import ask, delete_thread, find_lecture, get
from lecture_evals.golden import GoldenQuestion, GoldenSet
from lecture_evals.runs import SuiteResult, file_sha256
from lecture_llm.agents import Prompt, Usage, attr, render_slide
from lecture_llm.qa import render_outline

DEFAULT_DATASET = Path("evals/datasets/golden-qa/mit-6.0001-lecture-10.json")
JUDGE_PROMPTS = Path("prompts/evals")
ON_TARGET_S = 10.0
_SCORES = {"correct": 1.0, "partly": 0.5}


class Judgement(BaseModel):
    # The answer says the lecture doesn't cover the question.
    declined: bool
    verdict: Literal["correct", "partly", "wrong"]
    supported: bool
    unsupported: list[str]
    reason: str


class CitationSeen(BaseModel):
    label: str
    at_s: float
    valid: bool


class AnswerCheck(BaseModel):
    id: str
    kind: str
    question: str
    answer: str
    error: str | None
    citations: list[CitationSeen]
    # Answerable questions only: whether a valid citation lands near the golden span.
    on_target: bool | None
    judgement: Judgement | None
    first_token_ms: int | None
    total_ms: int | None
    tokens: int | None


class AnswersReport(BaseModel):
    dataset: str
    api_url: str
    lecture_id: uuid.UUID
    started_at: datetime
    answer_model: str | None
    judge_model: str
    results: list[AnswerCheck]


class Judge:
    def __init__(self, model: Model, prompts_dir: Path = JUDGE_PROMPTS) -> None:
        self.model_name = f"{model.system}:{model.model_name}"
        self.prompt = Prompt.load(prompts_dir, "judge-answer.v2")
        self._agent = Agent(model, output_type=Judgement, instructions=self.prompt.text)

    def grade(
        self, question: str, reference: str | None, passages: str, answer: str
    ) -> tuple[Judgement, Usage]:
        prompt = "\n\n".join(
            [
                f"<question>{html.escape(question)}</question>",
                f"<reference>{html.escape(reference or 'none')}</reference>",
                passages,
                f"<answer>{html.escape(answer)}</answer>",
            ]
        )
        result = self._agent.run_sync(prompt)
        usage = Usage()
        usage.add(result.usage)
        return result.output, usage


def render_evidence(
    sources: Sequence[dict[str, Any]],
    transcript: Sequence[dict[str, Any]],
    slides: Mapping[str, SlideReading] | None = None,
) -> str:
    """The passages an answer was given, rebuilt from its sources, the lecture's transcript and
    the slide on screen in each segment (`slides`, by segment id), as the answer model saw
    them. Without the slides, a claim read off a slide looks unsupported."""
    blocks = []
    for source in sources:
        span = f"{format_timestamp(source['start_s'])}-{format_timestamp(source['end_s'])}"
        slide = f" slide={attr(source['slide_title'])}" if source.get("slide_title") else ""
        lines = [f'<passage time="{span}"{slide}>']
        reading = (slides or {}).get(source.get("segment_id", ""))
        if reading is not None and (rendered := render_slide(reading)):
            lines.append(f"<slide>\n{rendered}\n</slide>")
        lines += [
            f"[{format_timestamp(line['start_s'])}] {html.escape(line['text'])}"
            for line in transcript
            if source["start_s"] - 0.5 <= line["start_s"] < source["end_s"]
        ]
        blocks.append("\n".join([*lines, "</passage>"]))
    return "<passages>\n" + "\n".join(blocks) + "\n</passages>"


def on_target(question: GoldenQuestion, citations: Sequence[CitationSeen]) -> bool:
    return any(
        c.valid and span.start_s - ON_TARGET_S <= c.at_s <= span.end_s + ON_TARGET_S
        for c in citations
        for span in question.spans
    )


def evaluate(
    client: httpx.Client,
    golden: GoldenSet,
    judge: Judge,
    lecture_id: uuid.UUID | None = None,
    dataset: str = "",
    pause_s: float = 0.0,
) -> AnswersReport:
    started_at = datetime.now(UTC)
    lecture_id = lecture_id or find_lecture(client, golden.lecture)
    transcript: list[dict[str, Any]] = get(client, f"/v1/lectures/{lecture_id}/transcript")
    # What the API gives the answer model besides the speech, rendered the same way: each
    # segment's slide, and the lecture's outline.
    readings = {
        s["slide_id"]: SlideReading(
            slide_id=s["slide_id"],
            title=s["title"],
            text=s["text"],
            figure_description=s["figure_description"],
            latex=s["latex"],
            code=s["code"],
        )
        for s in get(client, f"/v1/lectures/{lecture_id}/slides")
    }
    slides = {
        segment["segment_id"]: readings[segment["slide_id"]]
        for segment in get(client, f"/v1/lectures/{lecture_id}/timeline")
        if segment["slide_id"] in readings
    }
    notes = StudyNotes.model_validate(get(client, f"/v1/lectures/{lecture_id}/notes")["notes"])
    outline = render_outline(Outline(lecture_id=lecture_id, chapters=notes.chapters))
    results = []
    answer_model = None
    for question in golden.questions:
        events = ask(client, lecture_id, question.question)
        last = events[-1]
        answer = last.get("answer") or {}
        answer_model = answer.get("model") or answer_model
        citations = [
            CitationSeen(label=c["label"], at_s=c["at_s"], valid=c["valid"])
            for c in answer.get("citations") or []
        ]
        error = last.get("detail") if last["type"] == "error" else None
        judgement = None
        if error is None:
            evidence = render_evidence(answer.get("sources") or [], transcript, slides)
            evidence += "\n\n" + outline
            judgement, _ = judge.grade(
                question.question, question.answer, evidence, answer["content"]
            )
        usage = answer.get("usage") or {}
        results.append(
            AnswerCheck(
                id=question.id,
                kind=question.kind,
                question=question.question,
                answer=answer.get("content", ""),
                error=error,
                citations=citations,
                on_target=on_target(question, citations) if question.answerable else None,
                judgement=judgement,
                first_token_ms=answer.get("first_token_ms"),
                total_ms=answer.get("total_ms"),
                tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                if usage
                else None,
            )
        )
        # Leaving a thread behind isn't worth failing the run over.
        if events[0]["type"] == "start":
            with contextlib.suppress(httpx.HTTPError):
                delete_thread(client, events[0]["thread_id"])
        time.sleep(pause_s)
    return AnswersReport(
        dataset=dataset,
        api_url=str(client.base_url),
        lecture_id=lecture_id,
        started_at=started_at,
        answer_model=answer_model,
        judge_model=judge.model_name,
        results=results,
    )


def metrics(report: AnswersReport) -> dict[str, float]:
    results = report.results
    answerable = [r for r in results if r.kind != "unanswerable"]
    unanswerable = [r for r in results if r.kind == "unanswerable"]

    def declined(r: AnswerCheck) -> bool:
        return bool(r.judgement and r.judgement.declined)

    def score(r: AnswerCheck) -> float:
        if r.judgement is None or r.judgement.declined:
            return 0.0
        return _SCORES.get(r.judgement.verdict, 0.0)

    answered = [r for r in results if r.judgement and not r.judgement.declined]
    attempted = [r for r in answerable if r.judgement and not r.judgement.declined]
    citations = [c for r in results for c in r.citations]
    first_tokens = [r.first_token_ms for r in results if r.first_token_ms is not None]
    totals = [r.total_ms for r in results if r.total_ms is not None]
    tokens = [r.tokens for r in results if r.tokens is not None]
    values = {
        "correctness": statistics.fmean(score(r) for r in answerable) if answerable else 0.0,
        "faithfulness": statistics.fmean(
            bool(r.judgement and r.judgement.supported) for r in answered
        )
        if answered
        else 1.0,
        "false_declines": statistics.fmean(declined(r) for r in answerable) if answerable else 0.0,
        "declines_when_uncovered": statistics.fmean(
            declined(r) and not any(c.valid for c in r.citations) for r in unanswerable
        )
        if unanswerable
        else 1.0,
        "citations_valid": statistics.fmean(c.valid for c in citations) if citations else 1.0,
        "citations_on_target": statistics.fmean(bool(r.on_target) for r in attempted)
        if attempted
        else 0.0,
        "errors": float(sum(r.error is not None for r in results)),
    }
    if first_tokens:
        values["first_token_p50_ms"] = _percentile(first_tokens, 50)
        values["first_token_p95_ms"] = _percentile(first_tokens, 95)
    if totals:
        values["total_p50_ms"] = _percentile(totals, 50)
    if tokens:
        values["tokens_per_answer"] = statistics.fmean(tokens)
    return values


def _percentile(values: Sequence[int], percent: float) -> float:
    """Nearest-rank percentile: the smallest value at least `percent` of values are below or at."""
    ordered = sorted(values)
    return float(ordered[max(math.ceil(percent / 100 * len(ordered)) - 1, 0)])


def run(
    client: httpx.Client, dataset: Path, judge: Judge, out_dir: Path, pause_s: float = 0.0
) -> tuple[SuiteResult, AnswersReport]:
    report = evaluate(client, GoldenSet.load(dataset), judge, dataset=str(dataset), pause_s=pause_s)
    folder = out_dir / "answers"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dataset.stem}-{report.started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    result = SuiteResult(
        suite="answers",
        dataset=str(dataset),
        dataset_sha256=file_sha256(dataset),
        lecture_id=report.lecture_id,
        config={
            "answer_model": report.answer_model,
            "judge_model": report.judge_model,
            "judge_prompt": judge.prompt.fingerprint,
            "on_target_s": ON_TARGET_S,
        },
        metrics=metrics(report),
        report=str(path),
    )
    return result, report


def to_markdown(report: AnswersReport) -> str:
    values = metrics(report)
    lines = [
        f"{len(report.results)} questions, answered by {report.answer_model}, judged by "
        f"{report.judge_model} (not yet calibrated against hand grades).",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    lines += [f"| {name} | {_show(name, value)} |" for name, value in values.items()]
    wrong = [
        r
        for r in report.results
        if r.error
        or (r.judgement and r.judgement.verdict != "correct")
        or (r.kind != "unanswerable" and r.judgement and r.judgement.declined)
    ]
    if wrong:
        lines += ["", "Not fully right:"]
        lines += [
            f"- {r.id} ({_label(r)}): " + (r.judgement.reason if r.judgement else r.error or "")
            for r in wrong
        ]
    return "\n".join(lines)


def _label(r: AnswerCheck) -> str:
    if r.judgement is None:
        return "error"
    return "declined" if r.judgement.declined else r.judgement.verdict


def _show(name: str, value: float) -> str:
    if name.endswith("_ms"):
        return f"{value / 1000:.1f} s"
    if name in {"errors", "tokens_per_answer"}:
        return f"{value:.0f}"
    return f"{value:.2f}"
