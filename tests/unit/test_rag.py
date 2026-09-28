"""Search: chunking, the Qdrant index (in-memory mode) and hybrid search, with fake encoders."""

import json
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import pytest
from qdrant_client import QdrantClient

from lecture_core.notes import Chapter
from lecture_core.timeline import SlideReading, Timeline, TimelineSegment
from lecture_rag.chunks import build_chunks, chapter_at
from lecture_rag.encoders import (
    QUERY_INSTRUCTION,
    EmbeddedChunk,
    TEIEmbedder,
    TEIReranker,
    embed_chunks,
)
from lecture_rag.index import SearchIndex, point_id
from lecture_rag.search import Searcher, SearchMode
from tests.unit.fakes import FakeDense, FakeReranker, FakeSparse

LECTURE = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000002")


def _segment(n: int, transcript: str, slide_id: int | None) -> TimelineSegment:
    return TimelineSegment(
        id=f"s{n}",
        start_s=(n - 1) * 30.0,
        end_s=n * 30.0,
        transcript=transcript,
        slide_id=slide_id,
        words=[],
    )


def _slide(slide_id: int, title: str, text: str) -> SlideReading:
    return SlideReading(
        slide_id=slide_id, title=title, text=text, figure_description="", latex=[], code=""
    )


TIMELINE = Timeline(
    duration_s=90.0,
    segments=[
        _segment(1, "memoisation stores the results of subproblems so we never redo them", 1),
        _segment(2, "the running time grows linearly with the size of the input", 2),
        _segment(3, "binary search halves the list every step so it takes logarithmic time", None),
    ],
    slides=[
        _slide(1, "Dynamic programming", "- Memoisation: a cache of results"),
        _slide(2, "Orders of growth", "- O(n) is linear"),
    ],
)
CHAPTERS = [
    Chapter(title="Memoisation", start_s=0.0, end_s=30.0, summary=""),
    Chapter(title="Complexity", start_s=30.0, end_s=90.0, summary=""),
]
OTHER_TIMELINE = Timeline(
    duration_s=30.0,
    segments=[_segment(1, "memoisation in a different lecture about recursion", None)],
    slides=[],
)


def _embedded(timeline: Timeline) -> list[EmbeddedChunk]:
    return embed_chunks(build_chunks(timeline), FakeDense(), FakeSparse()).chunks


@pytest.fixture
def index() -> Iterator[SearchIndex]:
    index = SearchIndex(QdrantClient(":memory:"), "segments")
    yield index
    index.close()


@pytest.fixture
def searcher(index: SearchIndex) -> Searcher:
    index.replace_lecture(LECTURE, _embedded(TIMELINE), CHAPTERS)
    index.replace_lecture(OTHER, _embedded(OTHER_TIMELINE), [])
    return Searcher(index, FakeDense(), FakeSparse(), FakeReranker(), candidates=10)


def test_chunks_carry_slide_text_times_and_segment() -> None:
    first, _, last = build_chunks(TIMELINE)

    assert first.text.splitlines() == [
        "Slide: Dynamic programming",
        "- Memoisation: a cache of results",
        TIMELINE.segments[0].transcript,
    ]
    assert (first.segment_id, first.start_s, first.end_s, first.slide_id) == ("s1", 0, 30, 1)
    assert (first.slide_title, first.transcript) == (
        "Dynamic programming",
        TIMELINE.segments[0].transcript,
    )
    # No slide on screen: the transcript alone.
    assert last.text == TIMELINE.segments[2].transcript


def test_chapter_at() -> None:
    assert chapter_at(CHAPTERS, 0.0) == "Memoisation"
    assert chapter_at(CHAPTERS, 45.0) == "Complexity"
    assert chapter_at([], 45.0) is None


@pytest.mark.parametrize("mode", list(SearchMode))
def test_every_mode_finds_the_passage(searcher: Searcher, mode: SearchMode) -> None:
    hits = searcher.search("how long does binary search take", lecture_ids=[LECTURE], mode=mode)

    assert hits[0].segment_id == "s3"
    assert hits[0].lecture_id == LECTURE
    assert (hits[0].start_s, hits[0].end_s, hits[0].chapter) == (60.0, 90.0, "Complexity")
    assert (hits[0].slide_title, hits[0].transcript) == (None, TIMELINE.segments[2].transcript)


def test_search_stays_within_the_given_lectures(searcher: Searcher) -> None:
    hits = searcher.search("memoisation", lecture_ids=[OTHER], mode=SearchMode.HYBRID)
    assert {hit.lecture_id for hit in hits} == {OTHER}

    everywhere = searcher.search("memoisation", mode=SearchMode.HYBRID)
    assert {hit.lecture_id for hit in everywhere} == {LECTURE, OTHER}


def test_limit(searcher: Searcher) -> None:
    for mode in SearchMode:
        assert len(searcher.search("the", lecture_ids=[LECTURE], limit=2, mode=mode)) == 2


def test_reranker_decides_the_final_order(index: SearchIndex) -> None:
    class Backwards:
        """Prefers whatever came last."""

        def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
            return [float(i) for i in range(len(texts))]

    index.replace_lecture(LECTURE, _embedded(TIMELINE), CHAPTERS)
    searcher = Searcher(index, FakeDense(), FakeSparse(), Backwards(), candidates=10)
    query = "binary search takes logarithmic time"

    fused = searcher.search(query, limit=3, mode=SearchMode.HYBRID)
    reranked = searcher.search(query, limit=3, mode=SearchMode.RERANK)

    assert [h.segment_id for h in reranked] == [h.segment_id for h in reversed(fused)]
    assert [h.score for h in reranked] == [2.0, 1.0, 0.0]


def test_ties_come_back_in_lecture_order(
    index: SearchIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qdrant returns equal scores in any order; the index sorts them by time, before cutting
    the list to the limit."""
    index.replace_lecture(LECTURE, _embedded(TIMELINE), CHAPTERS)
    query_points = index.client.query_points

    def all_tied_and_reversed(*args: Any, **kwargs: Any) -> Any:
        response = query_points(*args, **kwargs)
        for point in response.points:
            point.score = 0.5
        response.points.reverse()
        return response

    monkeypatch.setattr(index.client, "query_points", all_tied_and_reversed)

    hits = index.dense(FakeDense().embed_query("anything"), [LECTURE], limit=2)
    assert [hit.segment_id for hit in hits] == ["s1", "s2"]


def test_reindexing_replaces_the_lectures_points(searcher: Searcher) -> None:
    index = searcher.index
    index.replace_lecture(LECTURE, _embedded(TIMELINE)[:1], CHAPTERS)

    # Only s3 mentioned binary search.
    assert searcher.search("binary search", lecture_ids=[LECTURE], mode=SearchMode.BM25) == []
    ids = {str(p.id) for p in index.client.scroll(index.collection, limit=100)[0]}
    assert ids == {point_id(LECTURE, "s1"), point_id(OTHER, "s1")}


def test_search_before_anything_is_indexed(index: SearchIndex) -> None:
    searcher = Searcher(index, FakeDense(), FakeSparse(), FakeReranker())
    assert searcher.search("anything") == []
    # Indexing nothing doesn't create the collection either.
    index.replace_lecture(LECTURE, [], [])
    assert not index.client.collection_exists(index.collection)


def test_a_different_embedding_size_is_refused(index: SearchIndex) -> None:
    index.replace_lecture(LECTURE, _embedded(TIMELINE), CHAPTERS)
    longer = [c.model_copy(update={"dense": [*c.dense, 0.0]}) for c in _embedded(TIMELINE)]

    with pytest.raises(ValueError, match="65"):
        index.replace_lecture(LECTURE, longer, CHAPTERS)


def test_point_ids_are_stable() -> None:
    assert point_id(LECTURE, "s1") == point_id(LECTURE, "s1")
    assert point_id(LECTURE, "s1") != point_id(OTHER, "s1")


def test_tei_embedder_batches_documents_and_instructs_queries() -> None:
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/info":
            return httpx.Response(200, json={"model_id": "Qwen/Q", "model_sha": "abcdef123456"})
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=[[float(len(text))] for text in body["inputs"]])

    embedder = TEIEmbedder("http://tei", batch_size=2, transport=httpx.MockTransport(respond))

    assert embedder.embed_documents(["a", "bb", "ccc"]) == [[1.0], [2.0], [3.0]]
    assert [r["inputs"] for r in requests] == [["a", "bb"], ["ccc"]]
    assert requests[0]["truncate"] is True
    embedder.embed_query("why?")
    assert requests[-1]["inputs"] == [QUERY_INSTRUCTION + "why?"]
    assert embedder.model_id == "Qwen/Q@abcdef1"


def test_tei_reranker_returns_scores_in_input_order() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        # TEI sorts by score, best first.
        ranked = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        return httpx.Response(
            200, json=[{"index": i, "score": float(len(texts[i]))} for i in ranked]
        )

    reranker = TEIReranker("http://tei", transport=httpx.MockTransport(respond))
    reranker.batch_size = 2

    assert reranker.rerank("q", ["a", "ccc", "bb"]) == [1.0, 3.0, 2.0]
    assert reranker.rerank("q", []) == []
