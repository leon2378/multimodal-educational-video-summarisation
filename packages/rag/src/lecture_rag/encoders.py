"""Turning text into vectors, and scoring passages against a query.

Dense embeddings and reranking come from Hugging Face Text Embeddings Inference (TEI) servers,
so the API and workers load no neural models. BM25 sparse vectors are computed in-process with
FastEmbed: they're token statistics, and Qdrant applies the IDF weighting.
"""

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel

from lecture_rag.chunks import Chunk

if TYPE_CHECKING:
    from fastembed import SparseEmbedding, SparseTextEmbedding

# Qwen3-Embedding works best with a task instruction on the query (not on the passages, so
# changing it doesn't mean re-embedding anything).
QUERY_INSTRUCTION = (
    "Instruct: Given a student's question about a lecture, retrieve the passage of the lecture "
    "that answers it\nQuery:"
)


class SparseVector(BaseModel):
    indices: list[int]
    values: list[float]


class EmbeddedChunk(Chunk):
    dense: list[float]
    sparse: SparseVector


class ChunkEmbeddings(BaseModel):
    """The embed stage's output (cached)."""

    chunks: list[EmbeddedChunk]


class DenseEncoder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SparseEncoder(Protocol):
    @property
    def model_id(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVector]: ...

    def embed_query(self, text: str) -> SparseVector: ...


class Reranker(Protocol):
    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """A relevance score for each text, in the order given."""
        ...


class TEIEmbedder:
    """Qwen3-Embedding-0.6B served by TEI (infra/compose.yaml).

    On a laptop CPU, TEI embeds about 95 tokens a second, so a batch of 8 chunks takes around
    30 seconds; the timeout leaves room for a busy server.
    """

    def __init__(
        self,
        url: str,
        batch_size: int = 8,
        timeout_s: float = 300,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._batch_size = batch_size
        self._http = httpx.Client(base_url=url, timeout=timeout_s, transport=transport)
        self._model_id: str | None = None

    @property
    def model_id(self) -> str:
        """The served model and revision, e.g. "Qwen/Qwen3-Embedding-0.6B@97b0c61". Read from
        the server, so a model change in Compose also changes the embedding cache key."""
        if self._model_id is None:
            response = self._http.get("/info")
            response.raise_for_status()
            info = response.json()
            self._model_id = f"{info['model_id']}@{str(info.get('model_sha') or '')[:7]}"
        return self._model_id

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors += self._embed(texts[start : start + self._batch_size])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_INSTRUCTION + text])[0]

    def close(self) -> None:
        self._http.close()

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._http.post(
            "/embed", json={"inputs": list(texts), "normalize": True, "truncate": True}
        )
        response.raise_for_status()
        vectors: list[list[float]] = response.json()
        return vectors


class TEIReranker:
    """A cross-encoder (bge-reranker-v2-m3) served by TEI. It reads the query and passage
    together, so it's slower than comparing embeddings but much sharper."""

    # TEI's default --max-client-batch-size.
    batch_size = 32

    def __init__(
        self, url: str, timeout_s: float = 120, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=url, timeout=timeout_s, transport=transport)

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        scores = [0.0] * len(texts)
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            response = self._http.post(
                "/rerank", json={"query": query, "texts": batch, "truncate": True}
            )
            response.raise_for_status()
            for item in response.json():
                scores[start + item["index"]] = item["score"]
        return scores

    def close(self) -> None:
        self._http.close()


class BM25Encoder:
    """BM25 term weights from FastEmbed's "Qdrant/bm25" (English stemming and stopwords).

    The model files (a few kB) are downloaded from Hugging Face on first use, not when the
    encoder is created, so the API starts without network access.
    """

    model_id = "Qdrant/bm25"

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._model: SparseTextEmbedding | None = None
        self._lock = threading.Lock()

    def embed_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [_sparse(e) for e in self._load().passage_embed(list(texts))]

    def embed_query(self, text: str) -> SparseVector:
        return _sparse(next(iter(self._load().query_embed(text))))

    def _load(self) -> "SparseTextEmbedding":
        with self._lock:
            if self._model is None:
                from fastembed import SparseTextEmbedding

                cache_dir = str(self._cache_dir) if self._cache_dir else None
                self._model = SparseTextEmbedding(self.model_id, cache_dir=cache_dir)
            return self._model


def _sparse(embedding: "SparseEmbedding") -> SparseVector:
    return SparseVector(
        indices=[int(i) for i in embedding.indices], values=[float(v) for v in embedding.values]
    )


def embed_chunks(
    chunks: Sequence[Chunk], dense: DenseEncoder, sparse: SparseEncoder
) -> ChunkEmbeddings:
    texts = [chunk.text for chunk in chunks]
    return ChunkEmbeddings(
        chunks=[
            EmbeddedChunk(**chunk.model_dump(), dense=dense_vector, sparse=sparse_vector)
            for chunk, dense_vector, sparse_vector in zip(
                chunks, dense.embed_documents(texts), sparse.embed_documents(texts), strict=True
            )
        ]
    )
