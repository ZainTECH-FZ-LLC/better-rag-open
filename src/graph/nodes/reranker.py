"""Reranking graph node — wraps BGEReranker for use in the LangGraph pipeline."""

from __future__ import annotations

import structlog

from src.models.state import AgentState

logger = structlog.get_logger()


async def reranker_node(state: AgentState) -> dict:
    """
    Re-score retrieved chunks with a cross-encoder and keep the top-k results.

    Reads:   state["retrieved_chunks"], state["query"]
    Writes:  state["retrieved_chunks"]  (replaced with reranked subset)
             state["rerank_scores"]     (list[float] for transparency / citation scoring)
    """
    chunks = state.get("retrieved_chunks", [])
    query = state.get("query", "")

    if not chunks or not query:
        logger.debug("reranker_node.skipped", reason="no chunks or query")
        return {}

    try:
        from src.retrieval.reranker import BGEReranker

        reranker = BGEReranker()
        top_k = state.get("rerank_top_k", 10)

        reranked = await reranker.rerank(query=query, candidates=chunks, top_k=top_k)

        logger.info(
            "reranker_node.completed",
            input_chunks=len(chunks),
            output_chunks=len(reranked),
        )

        return {
            "retrieved_chunks": [r.chunk for r in reranked],
            "rerank_scores": [r.rerank_score for r in reranked],
        }

    except Exception as exc:
        # Reranking is non-critical — fall through with original chunks on failure
        logger.warning("reranker_node.failed", error=str(exc))
        return {}
