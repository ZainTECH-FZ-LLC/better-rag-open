"""LangGraph node — deterministic retrieval strategy selection (zero LLM cost)."""

from __future__ import annotations

import structlog

from src.models.state import AgentState

logger = structlog.get_logger()

# Query type → retrieval strategy mapping
_STRATEGY_MAP = {
    "factual": "cosine",        # Fast, precise — what is X, who is Y
    "analytical": "hyde_cosine", # Deeper search — analyze, compare, explain
    "generative": "hyde_cosine", # Generation requests benefit from HyDE
    "procedural": "mmr",         # How-to queries need diverse, step-by-step results
}

# Department → context chunk count (from plan spec)
_DEPT_CONTEXT_CHUNKS = {
    "hr": 6,
    "finance": 8,
    "sales": 6,
    "marketing": 6,
    "general": 5,
}


async def smart_router_node(state: AgentState) -> dict:
    """
    Deterministic routing node — sets retrieval strategy without any LLM call.

    Logic:
    - query_type → retrieval_strategy (cosine | hyde_cosine | mmr)
    - target_department → context chunk count
    - Overrides: explicit "how to" → MMR, direct fact → cosine

    Updates state with:
    - retrieval_strategy: final strategy to use
    - metadata_filters: may add department filter if not already set
    """
    query_type = state.get("query_type", "factual")
    target_department = state.get("target_department", "general")
    original_query = state.get("original_query", "")
    current_strategy = state.get("retrieval_strategy")

    # Smalltalk — query analyzer already classified it, just short-circuit
    if query_type == "smalltalk":
        logger.info("node.smart_router.smalltalk", query=original_query[:80])
        return {
            "is_smalltalk": True,
            "retrieval_strategy": "none",
            "iteration_count": state.get("iteration_count", 0),
        }

    query_lower = original_query.lower()

    # Only override if query analyzer didn't already set a strategy
    if not current_strategy or current_strategy == "cosine":
        strategy = _STRATEGY_MAP.get(query_type, "cosine")

        # Keyword overrides (deterministic, zero-cost)
        how_to_keywords = ["how to", "how do i", "steps to", "guide", "tutorial", "procedure"]
        if any(kw in query_lower for kw in how_to_keywords):
            strategy = "mmr"

        compare_keywords = ["compare", "difference", "vs", "versus", "pros and cons", "trade-off"]
        if any(kw in query_lower for kw in compare_keywords):
            strategy = "hyde_cosine"
    else:
        strategy = current_strategy

    # Ensure department is set in metadata_filters if we know the target dept
    metadata_filters = dict(state.get("metadata_filters") or {})
    if target_department and not metadata_filters.get("department"):
        metadata_filters["department"] = target_department

    context_chunks = _DEPT_CONTEXT_CHUNKS.get(
        (target_department or "general").lower(), 5
    )

    logger.info(
        "node.smart_router",
        query_type=query_type,
        strategy=strategy,
        department=target_department,
        context_chunks=context_chunks,
    )

    return {
        "retrieval_strategy": strategy,
        "metadata_filters": metadata_filters,
        "iteration_count": state.get("iteration_count", 0),
    }
