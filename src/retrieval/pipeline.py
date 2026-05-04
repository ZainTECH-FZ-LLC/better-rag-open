"""Unified retrieval pipeline — orchestrates query analysis, vector search, graph expansion, and reranking."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.embedding.azure_openai import AzureOpenAIEmbedder
from src.knowledge_graph.builder import GraphBuilder
from src.retrieval.hyde import HyDEGenerator
from src.retrieval.query_analyzer import QueryAnalysis, QueryAnalyzer
from src.retrieval.reranker import CohereReranker, RerankedResult
from src.storage.vector_store import ChunkResult, PgVectorStore

logger = structlog.get_logger()


@dataclass
class RetrievalResult:
    """Complete retrieval pipeline output."""

    query_analysis: QueryAnalysis
    vector_results: list[ChunkResult] = field(default_factory=list)
    reranked_results: list[RerankedResult] = field(default_factory=list)
    graph_context: list[dict] = field(default_factory=list)
    graph_chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def final_chunks(self) -> list[ChunkResult]:
        """Get the final ranked chunk list (reranked if available, else raw vector)."""
        if self.reranked_results:
            return [r.chunk for r in self.reranked_results]
        return self.vector_results

    @property
    def all_chunks(self) -> list[ChunkResult]:
        """Final chunks + graph-expanded chunks for full answer context."""
        return self.final_chunks + self.graph_chunks

    @property
    def citations(self) -> list[dict]:
        """Build citation list from all chunks (vector + graph)."""
        seen_docs = set()
        citations = []
        for chunk in self.all_chunks:
            doc_key = chunk.document_id
            if doc_key not in seen_docs:
                seen_docs.add(doc_key)
                citations.append({
                    "document_id": chunk.document_id,
                    "document_title": chunk.document_title,
                    "sharepoint_url": chunk.sharepoint_url,
                    "department": chunk.department,
                    "chunk_id": chunk.chunk_id,
                    "page_numbers": chunk.page_numbers,
                    "section_heading": chunk.section_heading,
                    "score": chunk.score,
                })
        return citations


class RetrievalPipeline:
    """
    Full retrieval pipeline:

    1. Query analysis (intent + routing + metadata filters)
    2. Embedding (plain or HyDE)
    3. Vector search (cosine or MMR, RBAC-filtered)
    4. Graph expansion + reranking (concurrent)
    5. Graph chunk fetching (targeted search on related documents)
    """

    def __init__(
        self,
        db_session: AsyncSession,
        embedder: AzureOpenAIEmbedder | None = None,
        graph_builder: GraphBuilder | None = None,
        reranker: CohereReranker | None = None,
    ) -> None:
        self.settings = get_settings()
        self.db = db_session
        self._embedder = embedder or AzureOpenAIEmbedder()
        self._hyde = HyDEGenerator(self._embedder)
        self._vector_store = PgVectorStore(db_session)
        self._graph_builder = graph_builder or GraphBuilder()
        self._reranker = reranker or CohereReranker()
        self._query_analyzer = QueryAnalyzer()

    async def retrieve(
        self,
        query: str,
        user_id: str,
        user_department: str | None = None,
        k: int = 10,
        fetch_k: int = 60,
        skip_rerank: bool = False,
    ) -> RetrievalResult:
        """
        Execute the full retrieval pipeline.

        Args:
            query: User's natural language query.
            user_id: Entra ID user ID for RBAC filtering.
            user_department: User's department for routing.
            k: Final number of results to return.
            fetch_k: Number of candidates for reranking.
            skip_rerank: Skip the reranking step (for speed).
        """
        # 1. Query analysis
        analysis = await self._query_analyzer.analyze(query, user_department)
        logger.info(
            "retrieval.analysis",
            query_type=analysis.query_type,
            strategy=analysis.retrieval_strategy,
            department=analysis.target_department,
        )

        search_query = analysis.reformulated_query or query

        # 2. Generate embedding (plain or HyDE)
        if analysis.retrieval_strategy == "hyde_cosine":
            query_embedding = await self._hyde.generate_embedding(search_query)
        else:
            query_embedding = await self._embedder.embed_query(search_query)

        # 3. Vector search
        # Only use explicit metadata_filters.department (set by LLM analysis) as a hard filter.
        # target_department is for agent routing, not search filtering — using it as a filter
        # causes false negatives (e.g. finance query missing sales-labeled chunks).
        department_filter = analysis.metadata_filters.get("department")

        if analysis.retrieval_strategy == "mmr":
            vector_results = await self._vector_store.mmr_search(
                query_embedding=query_embedding,
                user_id=user_id,
                k=k if skip_rerank else fetch_k,
                fetch_k=fetch_k * 2,
                department=department_filter,
            )
        else:
            vector_results = await self._vector_store.cosine_search(
                query_embedding=query_embedding,
                user_id=user_id,
                k=k if skip_rerank else fetch_k,
                department=department_filter,
                content_type=analysis.metadata_filters.get("content_type"),
            )

        logger.info("retrieval.vector_search", results=len(vector_results))

        # 4. Graph expansion + reranking — run concurrently
        doc_ids = list({r.document_id for r in vector_results[:10]})

        async def _expand() -> list[dict]:
            if not vector_results:
                return []
            try:
                return await self._graph_builder.expand_from_documents(
                    doc_ids=doc_ids,
                    limit=5,
                )
            except Exception as e:
                logger.warning("retrieval.graph_expansion_failed", error=str(e))
                return []

        async def _rerank() -> list[RerankedResult]:
            if skip_rerank or not vector_results:
                return []
            try:
                return await self._reranker.rerank(
                    query=search_query,
                    candidates=vector_results,
                    top_k=k,
                )
            except Exception as e:
                logger.warning("retrieval.rerank_failed", error=str(e))
                return []

        graph_context, reranked_results = await asyncio.gather(_expand(), _rerank())

        # If reranking failed, fall back to diversified vector order
        if not skip_rerank and not reranked_results and vector_results:
            logger.info("retrieval.diversify_fallback", candidates=len(vector_results))
            vector_results = PgVectorStore.diversify(vector_results, top_k=k)

        # 5. Fetch chunks from graph-related documents
        graph_chunks: list[ChunkResult] = []
        if graph_context:
            already_ids = {r.document_id for r in (
                [rr.chunk for rr in reranked_results] if reranked_results else vector_results
            )}
            related_doc_ids = [
                ctx["doc_id"]
                for ctx in graph_context
                if ctx.get("doc_id") and ctx["doc_id"] not in already_ids
            ]
            if related_doc_ids:
                try:
                    graph_chunks = await self._vector_store.search_by_doc_ids(
                        query_embedding=query_embedding,
                        doc_ids=related_doc_ids,
                        top_n_per_doc=6,
                    )
                    logger.info("retrieval.graph_chunks", count=len(graph_chunks))
                except Exception as e:
                    logger.warning("retrieval.graph_chunks_failed", error=str(e))

        result = RetrievalResult(
            query_analysis=analysis,
            vector_results=vector_results,
            reranked_results=reranked_results,
            graph_context=graph_context,
            graph_chunks=graph_chunks,
        )

        logger.info(
            "retrieval.completed",
            vector_results=len(vector_results),
            reranked=len(reranked_results),
            graph_context=len(graph_context),
            graph_chunks=len(graph_chunks),
            citations=len(result.citations),
        )

        return result
