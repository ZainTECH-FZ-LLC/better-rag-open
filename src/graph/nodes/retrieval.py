"""LangGraph node — retrieval (vector search + graph expansion + reranking)."""

from __future__ import annotations

import structlog

from src.models.state import AgentState
from src.retrieval.pipeline import RetrievalPipeline
from src.storage.db import get_db_session

logger = structlog.get_logger()


async def retrieval_node(state: AgentState) -> dict:
    """
    Execute the retrieval pipeline — vector search, graph expansion, reranking,
    and graph chunk fetching.

    Reads:
    - original_query / reformulated_query
    - retrieval_strategy
    - metadata_filters
    - user_context.user_id

    Updates:
    - raw_results (vector search results)
    - reranked_results (after Cohere reranking, or diversified fallback)
    - graph_context (related documents from Neo4j)
    - graph_chunks (chunks fetched from graph-related documents)
    - citations
    """
    query = state.get("reformulated_query") or state.get("original_query", "")
    user_context = state.get("user_context", {})
    user_id = user_context.get("user_id", "anonymous")

    if not query:
        return {"raw_results": [], "reranked_results": [], "graph_context": [], "graph_chunks": [], "citations": []}

    async with get_db_session() as db:
        pipeline = RetrievalPipeline(db_session=db)
        result = await pipeline.retrieve(
            query=query,
            user_id=user_id,
            user_department=user_context.get("department"),
            k=10,
            fetch_k=60,
        )

    def _serialize_chunk(c):
        return {
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "content": c.content,
            "content_with_context": c.content_with_context,
            "chunk_type": c.chunk_type,
            "section_heading": c.section_heading,
            "department": c.department,
            "sharepoint_url": c.sharepoint_url,
            "document_title": c.document_title,
            "score": c.score,
            "page_numbers": c.page_numbers,
        }

    # Serialize results for state
    raw = [_serialize_chunk(c) for c in result.vector_results]

    reranked = [
        {
            **_serialize_chunk(r.chunk),
            "score": r.rerank_score,
            "original_rank": r.original_rank,
        }
        for r in result.reranked_results
    ]

    graph_chunk_dicts = [
        {**_serialize_chunk(c), "via_graph": True}
        for c in result.graph_chunks
    ]

    logger.info(
        "node.retrieval",
        raw=len(raw),
        reranked=len(reranked),
        graph=len(result.graph_context),
        graph_chunks=len(graph_chunk_dicts),
    )

    return {
        "raw_results": raw,
        "reranked_results": reranked,
        "graph_context": result.graph_context,
        "graph_chunks": graph_chunk_dicts,
        "citations": result.citations,
    }
