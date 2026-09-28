"""Hybrid search with reranking (blueprint section 5).

Dense vectors find passages that mean the same thing in other words; BM25 finds the exact
technical terms. Their top candidates are fused by reciprocal rank, then a cross-encoder
reranks the fused list. The other modes exist so the retrieval eval can compare them.
"""

import uuid
from collections.abc import Sequence
from enum import StrEnum

from lecture_rag.encoders import DenseEncoder, Reranker, SparseEncoder
from lecture_rag.index import Hit, SearchIndex


class SearchMode(StrEnum):
    DENSE = "dense"
    BM25 = "bm25"
    HYBRID = "hybrid"
    # Hybrid, then the reranker reorders the candidates.
    RERANK = "rerank"


class Searcher:
    def __init__(
        self,
        index: SearchIndex,
        dense: DenseEncoder,
        sparse: SparseEncoder,
        reranker: Reranker,
        candidates: int = 30,
        rrf_k: int = 60,
    ) -> None:
        self.index = index
        self.dense = dense
        self.sparse = sparse
        self.reranker = reranker
        # How many passages each retriever contributes, and how many get reranked.
        self.candidates = candidates
        # The constant from the RRF paper (Cormack et al., 2009); Qdrant's default is 2.
        self.rrf_k = rrf_k

    def search(
        self,
        query: str,
        *,
        lecture_ids: Sequence[uuid.UUID] | None = None,
        limit: int = 6,
        mode: SearchMode = SearchMode.RERANK,
    ) -> list[Hit]:
        """The `limit` best passages, optionally only from some lectures. Scores are only
        comparable within one mode."""
        if mode is SearchMode.DENSE:
            return self.index.dense(self.dense.embed_query(query), lecture_ids, limit)
        if mode is SearchMode.BM25:
            return self.index.sparse(self.sparse.embed_query(query), lecture_ids, limit)
        hits = self.index.hybrid(
            self.dense.embed_query(query),
            self.sparse.embed_query(query),
            lecture_ids,
            limit=max(limit, self.candidates) if mode is SearchMode.RERANK else limit,
            candidates=self.candidates,
            rrf_k=self.rrf_k,
        )
        if mode is SearchMode.HYBRID:
            return hits
        scores = self.reranker.rerank(query, [hit.text for hit in hits])
        ranked = sorted(zip(hits, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [hit.model_copy(update={"score": score}) for hit, score in ranked[:limit]]
