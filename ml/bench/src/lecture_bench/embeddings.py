"""Embeddings: Qwen3-Embedding-0.6B served by TEI, on the CPU and on the GPU.

Two speeds matter: embedding a lecture's chunks (the embed stage, on the CPU the pipeline's
slowest) and embedding one question (the first step of every answer). Search quality is checked
the way the retrieval eval scores it: dense search on the golden questions, Recall@5, MRR@10 and
nDCG@10, plus how close each variant's vectors are to the CPU's.
"""

import statistics
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
import numpy as np
from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from lecture_evals.golden import GoldenSet
from lecture_evals.suites.retrieval import K, QuestionResult, Segment, is_relevant, summarise
from lecture_rag.encoders import TEIEmbedder


@dataclass(frozen=True)
class Chunk:
    segment: Segment
    text: str


class EmbeddingResult(BaseModel):
    variant: str
    model: str
    chunks: int
    tokens: int
    embed_s: float
    tokens_per_s: float
    query_p50_ms: float
    recall_at_5: float
    mrr_at_10: float
    ndcg_at_10: float
    # Mean cosine similarity of each chunk's vector to the reference variant's.
    cosine_to_reference: float | None = None
    gpu_mb: float | None = None


def lecture_chunks(qdrant_url: str, collection: str, lecture_id: uuid.UUID) -> list[Chunk]:
    """A lecture's chunks as the index holds them: the text that was embedded, and its times."""
    client = QdrantClient(url=qdrant_url)
    wanted = models.Filter(
        must=[
            models.FieldCondition(key="lecture_id", match=models.MatchValue(value=str(lecture_id)))
        ]
    )
    points: list[models.Record] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection, scroll_filter=wanted, limit=256, offset=offset, with_payload=True
        )
        points += batch
        if offset is None:
            break
    chunks = []
    for point in points:
        payload = point.payload or {}
        segment = Segment(
            segment_id=payload["segment_id"], start_s=payload["start_s"], end_s=payload["end_s"]
        )
        chunks.append(Chunk(segment, payload["text"]))
    return sorted(chunks, key=lambda c: c.segment.start_s)


def token_count(url: str, texts: Sequence[str], batch: int = 16) -> int:
    """Tokens in the texts, by the server's own tokenizer (TEI takes at most 32 inputs a call)."""
    total = 0
    for start in range(0, len(texts), batch):
        response = httpx.post(
            f"{url}/tokenize", json={"inputs": list(texts[start : start + batch])}, timeout=120
        )
        response.raise_for_status()
        total += sum(len(tokens) for tokens in response.json())
    return total


def _normalised(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)
    normalised: np.ndarray = array / np.linalg.norm(array, axis=1, keepdims=True)
    return normalised


def benchmark(
    variant: str,
    url: str,
    chunks: Sequence[Chunk],
    golden: GoldenSet,
    tokens: int,
    reference: np.ndarray | None = None,
) -> tuple[EmbeddingResult, np.ndarray]:
    embedder = TEIEmbedder(url)
    embedder.embed_documents([chunks[0].text])  # load and warm up
    started = time.monotonic()
    vectors = _normalised(embedder.embed_documents([c.text for c in chunks]))
    embed_s = time.monotonic() - started

    results: list[QuestionResult] = []
    query_ms: list[float] = []
    for question in golden.questions:
        if not question.answerable:
            continue
        started = time.monotonic()
        query = _normalised([embedder.embed_query(question.question)])[0]
        query_ms.append((time.monotonic() - started) * 1000)
        order = np.argsort(-(vectors @ query))[:K]
        hits = [chunks[i].segment for i in order]
        results.append(
            QuestionResult(
                id=question.id,
                kind=question.kind,
                mode=variant,
                relevant_ranks=[
                    rank
                    for rank, hit in enumerate(hits, start=1)
                    if is_relevant(hit, question.spans)
                ],
                relevant_segments=sum(is_relevant(c.segment, question.spans) for c in chunks),
                top=[hit.segment_id for hit in hits],
                latency_ms=query_ms[-1],
            )
        )
    summary = summarise(variant, results)
    return (
        EmbeddingResult(
            variant=variant,
            model=embedder.model_id,
            chunks=len(chunks),
            tokens=tokens,
            embed_s=embed_s,
            tokens_per_s=tokens / embed_s,
            query_p50_ms=statistics.median(query_ms),
            recall_at_5=summary.recall_at_5,
            mrr_at_10=summary.mrr_at_10,
            ndcg_at_10=summary.ndcg_at_10,
            cosine_to_reference=(
                float(np.mean(np.sum(vectors * reference, axis=1)))
                if reference is not None
                else None
            ),
        ),
        vectors,
    )
