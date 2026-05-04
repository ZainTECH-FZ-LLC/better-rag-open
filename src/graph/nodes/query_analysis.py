"""LangGraph node — query analysis and routing."""

from __future__ import annotations

import structlog
from langchain_core.messages import AIMessage, HumanMessage

from src.models.state import AgentState
from src.retrieval.query_analyzer import QueryAnalyzer

logger = structlog.get_logger()


def _build_chat_summary(messages: list, max_turns: int = 5) -> str:
    """Build a short conversation summary from recent messages for context resolution."""
    recent = messages[-(max_turns * 2):]  # last N turns (user+assistant pairs)
    lines = []
    for msg in recent:
        if isinstance(msg, HumanMessage):
            lines.append(f"User: {msg.content[:200]}")
        elif isinstance(msg, AIMessage):
            lines.append(f"Assistant: {msg.content[:200]}")
    return "\n".join(lines)


async def query_analysis_node(state: AgentState) -> dict:
    """
    Analyze the user query to determine intent, routing, and retrieval strategy.

    If the query looks like a follow-up (short, uses pronouns, references "that"/"it"),
    the recent chat history is included so the analyzer can resolve it.

    Updates state with:
    - query_type
    - target_department
    - metadata_filters
    - retrieval_strategy
    - reformulated_query
    - requires_document_generation
    - document_output (if applicable)
    """
    messages = state["messages"]
    user_context = state.get("user_context", {})

    # Extract the latest user message
    query = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            query = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            query = msg.get("content", "")
            break

    if not query:
        return {"original_query": "", "query_type": "factual"}

    # Build chat history summary for follow-up resolution
    chat_history = _build_chat_summary(messages[:-1]) if len(messages) > 1 else None

    analyzer = QueryAnalyzer()
    analysis = await analyzer.analyze(
        query=query,
        user_department=user_context.get("department"),
        chat_history=chat_history,
    )

    updates = {
        "original_query": query,
        "query_type": analysis.query_type,
        "target_department": analysis.target_department,
        "metadata_filters": analysis.metadata_filters,
        "retrieval_strategy": analysis.retrieval_strategy,
        "reformulated_query": analysis.reformulated_query,
        "requires_document_generation": analysis.requires_document_generation,
    }

    if analysis.requires_document_generation and analysis.document_type:
        updates["document_output"] = {
            "doc_type": analysis.document_type,
            "template_name": None,
            "spec": {},
        }

    logger.info(
        "node.query_analysis",
        query_type=analysis.query_type,
        department=analysis.target_department,
        strategy=analysis.retrieval_strategy,
    )

    return updates
