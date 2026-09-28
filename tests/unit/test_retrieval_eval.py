import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from lecture_evals.golden import GoldenSet, Span
from lecture_evals.suites.retrieval import (
    QuestionResult,
    Segment,
    evaluate,
    is_relevant,
    ndcg_at,
    reciprocal_rank,
    to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LECTURE_ID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


def _golden(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "lecture": {
            "title": "T",
            "video": "v.mp4",
            "video_sha256": "ab" * 32,
            "duration": "02:00",
            "source": "https://example.org",
            "licence": "CC BY-NC-SA 4.0",
            "attribution": "Someone",
        },
        "status": "draft",
        "questions": [
            {
                "id": "q1",
                "kind": "fact",
                "question": "What is in the middle?",
                "answer": "The middle.",
                "spans": [["00:40", "00:50"]],
            },
            {"id": "u1", "kind": "unanswerable", "question": "?", "answer": None, "spans": []},
        ],
    }
    data.update(overrides)
    return data


def _result(ranks: list[int], relevant_segments: int = 1) -> QuestionResult:
    return QuestionResult(
        id="q",
        kind="fact",
        mode="dense",
        relevant_ranks=ranks,
        relevant_segments=relevant_segments,
        top=[],
        latency_ms=1.0,
    )


def test_the_committed_golden_set_is_valid() -> None:
    golden = GoldenSet.load(REPO_ROOT / "evals/datasets/golden-qa/mit-6.0001-lecture-10.json")
    assert sum(q.answerable for q in golden.questions) >= 30
    assert golden.questions[0].spans[0] == Span(start_s=129, end_s=140)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"id": "u1"}, "unique"),
        ({"spans": [["01:50", "02:10"]]}, "inside the video"),
        ({"answer": None}, "exactly when"),
    ],
)
def test_golden_set_checks(change: dict[str, Any], error: str) -> None:
    data = _golden()
    data["questions"][0].update(change)
    with pytest.raises(ValidationError, match=error):
        GoldenSet.model_validate(data)


def test_relevance_needs_a_few_seconds_of_overlap() -> None:
    spans = [Span(start_s=40, end_s=50)]

    assert is_relevant(Segment(segment_id="a", start_s=30, end_s=60), spans)
    assert is_relevant(Segment(segment_id="b", start_s=46, end_s=90), spans)  # 4 s
    assert not is_relevant(Segment(segment_id="c", start_s=48, end_s=90), spans)  # 2 s
    assert not is_relevant(Segment(segment_id="d", start_s=50, end_s=90), spans)  # touching
    # A 4-second span only needs half of it covered.
    assert is_relevant(Segment(segment_id="e", start_s=48, end_s=60), [Span(start_s=46, end_s=50)])


def test_ranking_metrics() -> None:
    assert reciprocal_rank(_result([3, 5])) == pytest.approx(1 / 3)
    assert reciprocal_rank(_result([])) == 0
    assert ndcg_at(_result([1], relevant_segments=1)) == 1
    # Two relevant segments exist, one was found at rank 2.
    assert ndcg_at(_result([2], relevant_segments=2)) == pytest.approx(0.6309 / 1.6309, 1e-3)
    assert ndcg_at(_result([11], relevant_segments=1)) == 0


def test_evaluate_through_the_api() -> None:
    golden = GoldenSet.model_validate(_golden())
    segments = [
        {"segment_id": f"s{i}", "start_s": i * 30.0, "end_s": i * 30.0 + 30, "transcript": ""}
        for i in range(4)
    ]
    searches: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/lectures":
            old = {"id": str(uuid.uuid4()), "updated_at": "2026-01-01T00:00:00Z"}
            new = {"id": str(LECTURE_ID), "updated_at": "2026-02-01T00:00:00Z"}
            ready = {"content_hash": "ab" * 32, "status": "ready"}
            return httpx.Response(200, json=[old | ready, new | ready])
        if path == f"/v1/lectures/{LECTURE_ID}/timeline":
            return httpx.Response(200, json=segments)
        assert path == "/v1/search"
        searches.append(dict(request.url.params))
        # Dense puts the answer (s1, 30-60 s) second; BM25 misses it.
        order = [0, 1, 2] if request.url.params["mode"] == "dense" else [3, 2, 0]
        return httpx.Response(200, json={"hits": [segments[i] for i in order]})

    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond))
    report = evaluate(client, golden, ["dense", "bm25"])

    assert report.lecture_id == LECTURE_ID
    dense, bm25 = report.summaries
    assert (dense.recall_at_5, dense.mrr_at_10) == (1.0, 0.5)
    assert (bm25.recall_at_5, bm25.mrr_at_10) == (0.0, 0.0)
    # One warm-up query per mode, then only the answerable question.
    assert [s["q"] for s in searches] == ["warm up", golden.questions[0].question] * 2
    assert searches[1] == {
        "q": "What is in the middle?",
        "lecture_id": str(LECTURE_ID),
        "limit": "10",
        "mode": "dense",
    }
    assert "| dense | 1.00 | 0.50 |" in to_markdown(report)
    json.loads(report.model_dump_json())
