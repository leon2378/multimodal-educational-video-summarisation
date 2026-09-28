"""The search index on a real Qdrant server, with the real BM25 encoder (FastEmbed downloads
its few kB of files from Hugging Face on first use)."""

import uuid
from collections.abc import Iterator

import pytest
from qdrant_client import QdrantClient

from lecture_rag.chunks import Chunk
from lecture_rag.encoders import BM25Encoder, embed_chunks
from lecture_rag.index import SearchIndex
from lecture_rag.search import Searcher, SearchMode
from tests.unit.fakes import FakeDense, FakeReranker

pytestmark = pytest.mark.integration

LECTURE = uuid.uuid4()
TEXTS = [
    "we will talk about lists and how a list grows",
    "merge sort splits the list in half and merges the sorted halves",
    "a hash table maps keys to values in constant time",
]


@pytest.fixture
def searcher(qdrant_url: str) -> Iterator[Searcher]:
    index = SearchIndex(QdrantClient(url=qdrant_url), f"test-{uuid.uuid4().hex[:8]}")
    bm25 = BM25Encoder()
    chunks = [
        Chunk(
            segment_id=f"s{i}",
            start_s=i * 30,
            end_s=i * 30 + 30,
            slide_id=None,
            slide_title=None,
            transcript=text,
            text=text,
        )
        for i, text in enumerate(TEXTS)
    ]
    index.replace_lecture(LECTURE, embed_chunks(chunks, FakeDense(), bm25).chunks, [])
    yield Searcher(index, FakeDense(), bm25, FakeReranker())
    index.client.delete_collection(index.collection)
    index.close()


def test_bm25_stems_and_weights_rare_terms(searcher: Searcher) -> None:
    # "sorting" matches "sort" and "sorted"; "lists" is in two passages, so it counts for less.
    hits = searcher.search("sorting lists", lecture_ids=[LECTURE], mode=SearchMode.BM25)
    assert [hit.segment_id for hit in hits] == ["s1", "s0"]


def test_bm25_ignores_stopwords(searcher: Searcher) -> None:
    assert searcher.search("the and of", mode=SearchMode.BM25) == []


@pytest.mark.parametrize("mode", [SearchMode.HYBRID, SearchMode.RERANK])
def test_fused_search_on_the_server(searcher: Searcher, mode: SearchMode) -> None:
    hits = searcher.search("hash table", lecture_ids=[LECTURE], mode=mode)
    assert hits[0].segment_id == "s2"
    assert searcher.search("hash table", lecture_ids=[uuid.uuid4()], mode=mode) == []
