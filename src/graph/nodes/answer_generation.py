"""LangGraph node — answer generation with department sub-agent routing."""

from __future__ import annotations

import structlog

from src.agents.department_agent import get_department_agent
from src.models.state import AgentState

logger = structlog.get_logger()


async def answer_generation_node(state: AgentState) -> dict:
    """
    Generate the final answer using the appropriate department sub-agent.

    Routes to a specialized department agent (HR, Finance, Sales, Marketing, General)
    based on the query analysis. Each sub-agent has domain-specific system prompts
    and citation patterns.

    Uses:
    - target_department (from query analysis)
    - reranked_results (or raw_results as fallback)
    - graph_context
    - original_query

    Updates:
    - answer
    - current_agent
    """
    query = state.get("original_query", "")
    is_smalltalk = state.get("is_smalltalk", False)
    target_department = "smalltalk" if is_smalltalk else state.get("target_department")

    results = state.get("reranked_results") or state.get("raw_results", [])
    graph_chunks = state.get("graph_chunks", [])
    graph_context = state.get("graph_context", [])

    # Merge reranked results + graph chunks for full context
    all_chunks = results + graph_chunks

    # Get department-specific agent (smalltalk uses its own prompt, no context needed)
    agent = get_department_agent(target_department)

    answer = await agent.generate_answer(
        query=query,
        context_chunks=all_chunks,
        graph_context=graph_context,
    )

    logger.info(
        "node.answer_generation",
        department=agent.department,
        query_length=len(query),
        context_chunks=len(results),
        answer_length=len(answer),
    )

    return {
        "answer": answer,
        "current_agent": agent.department,
    }
