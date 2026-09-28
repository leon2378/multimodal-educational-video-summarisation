"""Retrieval eval: does search put the passage that answers a question near the top?

Sends each golden question to the API's search endpoint in every mode and scores the hits
against the answer spans (blueprint section 8): Recall@5, MRR@10 and nDCG@10, plus latency.
A hit counts as relevant when its segment overlaps an answer span by at least 3 seconds (or by
half the span, for spans under 6 seconds).

    uv run retrieval-eval                  # the Lecture 10 golden set, against localhost:8000
    uv run retrieval-eval --modes dense,rerank
"""

import argparse
import math
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from lecture_core.notes import format_timestamp
from lecture_evals.golden import GoldenQuestion, GoldenSet, Span

MODES = ("dense", "bm25", "hybrid", "rerank")
DEFAULT_DATASET = Path("evals/datasets/golden-qa/mit-6.0001-lecture-10.json")
DEFAULT_OUT = Path("data/evals/retrieval")
MIN_OVERLAP_S = 3.0
K = 10


class Segment(BaseModel):
    segment_id: str
    start_s: float
    end_s: float


class QuestionResult(BaseModel):
    id: str
    kind: str
    mode: str
    # 1-based ranks of the relevant hits in the top K.
    relevant_ranks: list[int]
    # How many of the lecture's segments are relevant (for nDCG).
    relevant_segments: int
    top: list[str]
    latency_ms: float


class ModeSummary(BaseModel):
    mode: str
    questions: int
    recall_at_5: float
    mrr_at_10: float
    ndcg_at_10: float
    median_latency_ms: float
    recall_at_5_by_kind: dict[str, float]


class RetrievalReport(BaseModel):
    dataset: str
    dataset_status: str
    api_url: str
    lecture_id: uuid.UUID
    segments: int
    started_at: datetime
    summaries: list[ModeSummary]
    results: list[QuestionResult]


def is_relevant(segment: Segment, spans: Sequence[Span]) -> bool:
    for span in spans:
        overlap = min(segment.end_s, span.end_s) - max(segment.start_s, span.start_s)
        if overlap > 0 and overlap >= min(MIN_OVERLAP_S, span.length_s / 2):
            return True
    return False


def recall_at(result: QuestionResult, k: int) -> float:
    return 1.0 if any(rank <= k for rank in result.relevant_ranks) else 0.0


def reciprocal_rank(result: QuestionResult, k: int = K) -> float:
    ranks = [rank for rank in result.relevant_ranks if rank <= k]
    return 1 / min(ranks) if ranks else 0.0


def ndcg_at(result: QuestionResult, k: int = K) -> float:
    dcg = sum(1 / math.log2(rank + 1) for rank in result.relevant_ranks if rank <= k)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(result.relevant_segments, k) + 1))
    return dcg / ideal if ideal else 0.0


def summarise(mode: str, results: Sequence[QuestionResult]) -> ModeSummary:
    kinds = sorted({r.kind for r in results})
    return ModeSummary(
        mode=mode,
        questions=len(results),
        recall_at_5=statistics.fmean(recall_at(r, 5) for r in results),
        mrr_at_10=statistics.fmean(reciprocal_rank(r) for r in results),
        ndcg_at_10=statistics.fmean(ndcg_at(r) for r in results),
        median_latency_ms=statistics.median(r.latency_ms for r in results),
        recall_at_5_by_kind={
            kind: statistics.fmean(recall_at(r, 5) for r in results if r.kind == kind)
            for kind in kinds
        },
    )


def find_lecture(client: httpx.Client, golden: GoldenSet) -> uuid.UUID:
    """The most recently processed lecture made from the golden set's video."""
    lectures: list[dict[str, Any]] = _get(client, "/v1/lectures")
    matches = [
        lecture
        for lecture in lectures
        if lecture["content_hash"] == golden.lecture.video_sha256 and lecture["status"] == "ready"
    ]
    if not matches:
        raise LookupError(
            f"no processed lecture has the video {golden.lecture.video}: upload and process it"
        )
    return uuid.UUID(max(matches, key=lambda lecture: lecture["updated_at"])["id"])


def evaluate(
    client: httpx.Client,
    golden: GoldenSet,
    modes: Sequence[str],
    lecture_id: uuid.UUID | None = None,
    dataset: str = "",
) -> RetrievalReport:
    started_at = datetime.now(UTC)
    lecture_id = lecture_id or find_lecture(client, golden)
    segments = [
        Segment.model_validate(s) for s in _get(client, f"/v1/lectures/{lecture_id}/timeline")
    ]
    if not segments:
        raise LookupError(f"lecture {lecture_id} has no timeline yet")
    questions = [q for q in golden.questions if q.answerable]
    results: list[QuestionResult] = []
    for mode in modes:
        # A first query loads anything lazy (the BM25 model, connection pools) outside the timings.
        _search(client, lecture_id, "warm up", mode)
        results += [_ask(client, lecture_id, question, segments, mode) for question in questions]
    return RetrievalReport(
        dataset=dataset,
        dataset_status=golden.status,
        api_url=str(client.base_url),
        lecture_id=lecture_id,
        segments=len(segments),
        started_at=started_at,
        summaries=[summarise(mode, [r for r in results if r.mode == mode]) for mode in modes],
        results=results,
    )


def _ask(
    client: httpx.Client,
    lecture_id: uuid.UUID,
    question: GoldenQuestion,
    segments: Sequence[Segment],
    mode: str,
) -> QuestionResult:
    started = time.perf_counter()
    hits = [
        Segment.model_validate(hit) for hit in _search(client, lecture_id, question.question, mode)
    ]
    latency_ms = (time.perf_counter() - started) * 1000
    return QuestionResult(
        id=question.id,
        kind=question.kind,
        mode=mode,
        relevant_ranks=[
            rank for rank, hit in enumerate(hits, start=1) if is_relevant(hit, question.spans)
        ],
        relevant_segments=sum(is_relevant(s, question.spans) for s in segments),
        top=[f"{hit.segment_id}@{format_timestamp(hit.start_s)}" for hit in hits[:5]],
        latency_ms=latency_ms,
    )


def _search(client: httpx.Client, lecture_id: uuid.UUID, query: str, mode: str) -> list[Any]:
    params: dict[str, str | int] = {
        "q": query,
        "lecture_id": str(lecture_id),
        "limit": K,
        "mode": mode,
    }
    hits: list[Any] = _get(client, "/v1/search", params=params)["hits"]
    return hits


def _get(client: httpx.Client, path: str, params: dict[str, str | int] | None = None) -> Any:
    response = client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def to_markdown(report: RetrievalReport) -> str:
    lines = [
        f"{report.summaries[0].questions if report.summaries else 0} questions, "
        f"{report.segments} segments, K = {K}",
        "",
        "| Mode | Recall@5 | MRR@10 | nDCG@10 | Median latency |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {s.mode} | {s.recall_at_5:.2f} | {s.mrr_at_10:.2f} | {s.ndcg_at_10:.2f} "
        f"| {s.median_latency_ms:.0f} ms |"
        for s in report.summaries
    ]
    kinds = sorted({kind for s in report.summaries for kind in s.recall_at_5_by_kind})
    lines += ["", "Recall@5 by kind of question:", "", "| Mode | " + " | ".join(kinds) + " |"]
    lines.append("|---|" + "---|" * len(kinds))
    lines += [
        f"| {s.mode} | "
        + " | ".join(f"{s.recall_at_5_by_kind.get(kind, 0):.2f}" for kind in kinds)
        + " |"
        for s in report.summaries
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrieval-eval", description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--lecture-id", type=uuid.UUID, help="default: found by video hash")
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report directory")
    args = parser.parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if unknown := [m for m in modes if m not in MODES]:
        parser.error(f"unknown mode(s): {', '.join(unknown)}")

    golden = GoldenSet.load(args.dataset)
    with httpx.Client(base_url=args.api_url, timeout=120) as client:
        try:
            report = evaluate(client, golden, modes, args.lecture_id, dataset=str(args.dataset))
        except (httpx.HTTPError, LookupError) as error:
            print(error, file=sys.stderr)
            return 1

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.strftime("%Y%m%dT%H%M%SZ")
    path = args.out / f"{args.dataset.stem}-{stamp}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(to_markdown(report))
    print(f"\nSaved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
