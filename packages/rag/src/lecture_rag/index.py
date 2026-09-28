"""The search index: every lecture's chunks in one Qdrant collection (ADR 0005).

Each point holds a dense vector (matches meaning) and a BM25 sparse vector (matches exact terms),
with the lecture, times, slide and chapter as payload. Point ids are derived from the lecture
and segment ids, so indexing a lecture again overwrites its points in place.
"""

import uuid
from collections.abc import Sequence

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from lecture_core.notes import Chapter
from lecture_rag.chunks import chapter_at
from lecture_rag.encoders import EmbeddedChunk, SparseVector

DENSE = "dense"
SPARSE = "bm25"

# Fixed namespace for point ids (uuid5 of lecture id and segment id).
_POINT_IDS = uuid.UUID("0d7f3a52-9b41-4c1e-8f0a-6e2d5c9b7a14")
_UPSERT_BATCH = 64
# Extra results fetched so that ties at the cut-off are settled here, not by Qdrant.
_TIE_MARGIN = 10


class _Payload(BaseModel):
    lecture_id: uuid.UUID
    segment_id: str
    start_s: float
    end_s: float
    slide_id: int | None
    slide_title: str | None
    chapter: str | None
    transcript: str
    text: str


class Hit(_Payload):
    score: float


def point_id(lecture_id: uuid.UUID, segment_id: str) -> str:
    return str(uuid.uuid5(_POINT_IDS, f"{lecture_id}/{segment_id}"))


class SearchIndex:
    def __init__(self, client: QdrantClient, collection: str) -> None:
        self.client = client
        self.collection = collection

    def close(self) -> None:
        self.client.close()

    # Writing

    def replace_lecture(
        self, lecture_id: uuid.UUID, chunks: Sequence[EmbeddedChunk], chapters: Sequence[Chapter]
    ) -> None:
        """Make the lecture's points exactly `chunks`. New points are written before stale ones
        are deleted, so searches never see the lecture half-indexed or missing."""
        if chunks:
            self._ensure_collection(len(chunks[0].dense))
        elif not self.client.collection_exists(self.collection):
            return
        points = [
            models.PointStruct(
                id=point_id(lecture_id, chunk.segment_id),
                vector={DENSE: chunk.dense, SPARSE: _qdrant_sparse(chunk.sparse)},
                payload=_Payload(
                    lecture_id=lecture_id,
                    segment_id=chunk.segment_id,
                    start_s=chunk.start_s,
                    end_s=chunk.end_s,
                    slide_id=chunk.slide_id,
                    slide_title=chunk.slide_title,
                    chapter=chapter_at(chapters, chunk.start_s),
                    transcript=chunk.transcript,
                    text=chunk.text,
                ).model_dump(mode="json"),
            )
            for chunk in chunks
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            self.client.upsert(self.collection, points[start : start + _UPSERT_BATCH], wait=True)
        stale = models.Filter(
            must=[_lecture_is([lecture_id])],
            must_not=[models.HasIdCondition(has_id=[p.id for p in points])],
        )
        self.client.delete(self.collection, models.FilterSelector(filter=stale), wait=True)

    def _ensure_collection(self, dense_size: int) -> None:
        if not self.client.collection_exists(self.collection):
            try:
                self.client.create_collection(
                    self.collection,
                    vectors_config={
                        DENSE: models.VectorParams(size=dense_size, distance=models.Distance.COSINE)
                    },
                    # Qdrant computes BM25's inverse document frequency across the collection.
                    sparse_vectors_config={
                        SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
                    },
                )
                self.client.create_payload_index(
                    self.collection, "lecture_id", models.PayloadSchemaType.KEYWORD
                )
            except Exception:
                # Another worker may have created it first; anything else is a real error.
                if not self.client.collection_exists(self.collection):
                    raise
        vectors = self.client.get_collection(self.collection).config.params.vectors
        size = vectors[DENSE].size if isinstance(vectors, dict) else None
        if size != dense_size:
            raise ValueError(
                f"collection {self.collection!r} holds {size}-dimensional dense vectors but the "
                f"embedding model gives {dense_size}: delete the collection and index again"
            )

    # Searching. Each returns [] until anything has been indexed.

    def dense(
        self, query: list[float], lecture_ids: Sequence[uuid.UUID] | None, limit: int
    ) -> list[Hit]:
        return self._query(query=query, using=DENSE, lecture_ids=lecture_ids, limit=limit)

    def sparse(
        self, query: SparseVector, lecture_ids: Sequence[uuid.UUID] | None, limit: int
    ) -> list[Hit]:
        return self._query(
            query=_qdrant_sparse(query), using=SPARSE, lecture_ids=lecture_ids, limit=limit
        )

    def hybrid(
        self,
        dense: list[float],
        sparse: SparseVector,
        lecture_ids: Sequence[uuid.UUID] | None,
        limit: int,
        candidates: int,
        rrf_k: int,
    ) -> list[Hit]:
        """The top `candidates` by each vector, fused by reciprocal rank (RRF)."""
        where = _where(lecture_ids)
        return self._query(
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, filter=where, limit=candidates),
                models.Prefetch(
                    query=_qdrant_sparse(sparse), using=SPARSE, filter=where, limit=candidates
                ),
            ],
            query=models.RrfQuery(rrf=models.Rrf(k=rrf_k)),
            lecture_ids=lecture_ids,
            limit=limit,
        )

    def _query(
        self,
        *,
        query: list[float] | models.SparseVector | models.RrfQuery,
        lecture_ids: Sequence[uuid.UUID] | None,
        limit: int,
        using: str | None = None,
        prefetch: list[models.Prefetch] | None = None,
    ) -> list[Hit]:
        if not self.client.collection_exists(self.collection):
            return []
        response = self.client.query_points(
            self.collection,
            query=query,
            using=using,
            prefetch=prefetch,
            query_filter=_where(lecture_ids),
            limit=limit + _TIE_MARGIN,
            with_payload=True,
        )
        hits = [
            Hit.model_validate({**(point.payload or {}), "score": point.score})
            for point in response.points
        ]
        # Qdrant returns equal scores in no fixed order, and reciprocal rank fusion ties often
        # (first in one list and second in the other scores the same as the reverse). Order ties
        # by where they are in the lecture, so the same search always gives the same results.
        hits.sort(key=lambda hit: (-hit.score, str(hit.lecture_id), hit.start_s))
        return hits[:limit]


def _qdrant_sparse(vector: SparseVector) -> models.SparseVector:
    return models.SparseVector(indices=vector.indices, values=vector.values)


def _lecture_is(lecture_ids: Sequence[uuid.UUID]) -> models.FieldCondition:
    return models.FieldCondition(
        key="lecture_id", match=models.MatchAny(any=[str(i) for i in lecture_ids])
    )


def _where(lecture_ids: Sequence[uuid.UUID] | None) -> models.Filter | None:
    return None if lecture_ids is None else models.Filter(must=[_lecture_is(lecture_ids)])
