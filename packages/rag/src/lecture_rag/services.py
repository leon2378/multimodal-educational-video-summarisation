"""Clients for the search services, configured from Settings.

Creating them makes no requests, so the API and workers start while Qdrant or the embedding
servers are still coming up.
"""

from dataclasses import dataclass

from qdrant_client import QdrantClient

from lecture_core.settings import Settings
from lecture_rag.encoders import BM25Encoder, TEIEmbedder, TEIReranker
from lecture_rag.index import SearchIndex
from lecture_rag.search import Searcher


@dataclass
class SearchServices:
    index: SearchIndex
    dense: TEIEmbedder
    sparse: BM25Encoder
    reranker: TEIReranker

    @classmethod
    def from_settings(cls, settings: Settings) -> "SearchServices":
        # The version check would be a request; client and server versions are pinned together.
        qdrant = QdrantClient(url=settings.qdrant_url, check_compatibility=False)
        return cls(
            index=SearchIndex(qdrant, settings.qdrant_collection),
            dense=TEIEmbedder(
                settings.embeddings_url,
                headers={
                    name: value.get_secret_value()
                    for name, value in settings.embeddings_headers.items()
                },
            ),
            sparse=BM25Encoder(),
            reranker=TEIReranker(settings.reranker_url),
        )

    def searcher(self) -> Searcher:
        return Searcher(self.index, self.dense, self.sparse, self.reranker)

    def close(self) -> None:
        self.index.close()
        self.dense.close()
        self.reranker.close()
