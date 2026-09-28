# 0005: Qdrant for hybrid search, next to Postgres

- Status: Accepted
- Date: 2026-09-29
- Code: `packages/rag`

## Context

Q&A needs hybrid retrieval over timeline segments (blueprint section 5): dense vectors for
questions worded differently from the lecture, BM25 for exact technical terms, the two fused,
then a reranker. Results must be filterable by lecture, and later by course. The data is small: a
lecture gives 40 to 80 segments, a 25-lecture course about 2,000.

## Options

- **pgvector in the existing Postgres.** One store fewer, and indexing could share a transaction
  with the results tables. Dense search is fine at this size (exact, or HNSW later). But
  Postgres has no BM25: its full-text ranking (`ts_rank`) ignores how rare a term is across the
  collection, which is the part that makes "memoisation" count for more than "list". BM25 needs
  an extension (ParadeDB's `pg_search`, VectorChord-BM25), which many managed Postgres services
  don't offer, and fusion would be hand-written SQL.
- **Qdrant.** Dense and sparse vectors live on the same point. Qdrant computes the IDF part of
  BM25 itself (`Modifier.IDF`), so the index only stores term frequencies, and one query fetches
  candidates from both and fuses them (prefetch plus reciprocal rank fusion). Payload filters have
  indexes. It's one more service, and it isn't transactional with Postgres.
- **OpenSearch or Elasticsearch.** Strong BM25 and vector search, but a JVM service that wants
  gigabytes of memory, which is a lot for a laptop stack.

## Decision

Qdrant 1.19.1 (Apache-2.0), one collection `segments` with a point per timeline segment:

- a 1024-dimensional cosine vector from Qwen3-Embedding-0.6B (named `dense`), and BM25 term
  weights from FastEmbed's `Qdrant/bm25` (named `bm25`, IDF applied by Qdrant);
- payload: lecture id, start and end times, slide id and title, chapter, and the text;
- hybrid search takes the top 30 from each vector, fuses them with RRF (k = 60, the constant from
  Cormack et al.; Qdrant's default is 2), and bge-reranker-v2-m3 reorders the fused list.

The embedding model and the reranker run in Text Embeddings Inference (TEI) containers, so no
Python process loads a neural model: locally, the embedding model on the CPU and the reranker on
the GPU (see below). Postgres stays the source of truth: the vectors are cached in the stage
cache (the `embed` stage), so the index can be rebuilt without re-embedding.

## Consequences

- Two stores to keep in step. The workflow indexes a lecture before marking it ready. Point ids
  come from the lecture and segment ids, and new points are written before stale ones are
  deleted, so indexing again is safe and searches never see a lecture half-indexed. Deleting a
  lecture (not implemented yet) must delete its points too.
- pgvector wasn't measured. At this size both stores search dense vectors exactly, so dense recall
  would be the same; what differs is BM25 and fusion, which Postgres would need an extension for.
- The retrieval eval (`make eval-retrieval`) scores each mode on the golden Q&A set. Lecture 10,
  33 questions, 47 segments, latency through the API with the reranker on the GPU:

  | Mode | Recall@5 | MRR@10 | nDCG@10 | Median latency |
  |---|---|---|---|---|
  | dense | 0.97 | 0.84 | 0.82 | 0.4 s |
  | BM25 | 0.91 | 0.76 | 0.74 | 8 ms |
  | hybrid (RRF) | 0.97 | 0.84 | 0.84 | 0.4 s |
  | hybrid + rerank | **1.00** | **0.89** | **0.86** | 0.8 s |

  With one lecture there are only 47 passages to choose from, and one question moves Recall@5
  by 0.03, so dense and hybrid are tied here. BM25 alone is weaker on paraphrased questions
  (Recall@5 0.80 against 1.00). The reranker is the only step that clearly helps: it moves 7
  questions up, including the one answer hybrid left outside the top 5 (8th to 2nd), and 4 down
  by one place each, and puts the answer first for 26 questions against hybrid's 24. Searching a
  whole course, with far more passages, is where hybrid should earn its place; the golden set
  needs more lectures to show it.
- The reranker runs on the GPU, where the plan had it on the CPU locally. On the CPU (a Ryzen 7
  5800H) TEI managed about 95 tokens a second, and reranking a query's 30 passages took 77
  seconds. On the RTX 3060 it takes about 0.4 seconds and holds 1.3 GB of VRAM, next to speech
  recognition's 2.3 GB peak. Compose runs it in the `gpu` profile and sets `SEARCH_MODE=rerank`;
  without a GPU, `hybrid` loses little on this set.
- The embedding model stays on the CPU, off the GPU that speech recognition needs. Embedding
  Lecture 10 takes about 5 minutes, alongside the notes, but the lecture is only marked ready
  once it's indexed, so a fresh run takes about 6 minutes rather than 2. The CPU backend has no
  flash attention, so TEI's default batch (`--max-batch-tokens 16384`) ran out of memory while
  warming up; Compose sets 4096, which also caps inputs at 4096 tokens (chunks are well under
  1,000).
- Qdrant returns equal scores in no fixed order, and reciprocal rank fusion ties often (first in
  one list and second in the other scores the same as the reverse), so the same search could
  rank differently from one call to the next. The index orders ties by position in the lecture,
  and two eval runs now give identical results.
- Unit tests use Qdrant's in-memory mode with fake encoders; integration tests run the real
  server and the real BM25 encoder.
