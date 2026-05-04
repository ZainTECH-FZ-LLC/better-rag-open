"""Customer Care retrieval pipeline — full pipeline using CC-specific vector store."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.customer_care.vector_store import CCVectorStore
from src.retrieval.pipeline import RetrievalPipeline


class CCRetrievalPipeline(RetrievalPipeline):
    """
    Full retrieval pipeline for the Customer Care knowledge base.

    Inherits all retrieval logic from RetrievalPipeline:
    - QueryAnalyzer: query type classification, reformulation, strategy selection
    - HyDEGenerator: hypothetical document embedding for analytical queries
    - CohereReranker: cross-encoder reranking
    - GraphBuilder.expand_from_documents: Neo4j graph expansion

    The only difference is the vector store: CCVectorStore queries
    cc_document_chunks / cc_document_user_access instead of the main tables.
    """

    def __init__(
        self,
        db_session: AsyncSession,
        embedder=None,
        graph_builder=None,
        reranker=None,
    ) -> None:
        super().__init__(db_session, embedder, graph_builder, reranker)
        # Swap in the CC-specific vector store
        self._vector_store = CCVectorStore(db_session)
