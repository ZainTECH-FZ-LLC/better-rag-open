"""LangGraph conditional edge routing functions."""

from __future__ import annotations

import structlog

from src.models.state import AgentState

logger = structlog.get_logger()

# Maximum retrieval retry attempts before forcing an answer
_MAX_RETRY_ITERATIONS = 1


def route_after_query_analysis(state: AgentState) -> str:
    """
    After query analysis, decide whether to go straight to doc generation
    or proceed with retrieval.

    Returns: "doc_generation" | "smart_router"
    """
    if state.get("requires_document_generation") and not state.get("raw_results"):
        # Pure generation request with no prior retrieval needed
        logger.debug("edge.route_to_doc_generation")
        return "doc_generation"
    logger.debug("edge.route_to_smart_router")
    return "smart_router"


def route_after_smart_router(state: AgentState) -> str:
    """
    After routing, always proceed to retrieval.
    (Smart router only sets the retrieval_strategy; retrieval always follows.)

    Returns: "retrieval"
    """
    return "retrieval"


def route_after_relevance_grade(state: AgentState) -> str:
    """
    After grading retrieval quality, decide whether to retry or proceed to dept agents.

    Retry logic:
    - If fewer than 2 relevant results AND we haven't retried yet → retry with HyDE
    - Otherwise → route to department agent

    Returns: "retrieval" | department name (hr|finance|sales|marketing|general)
    """
    should_retry = state.get("should_retry_retrieval", False)
    iteration = state.get("iteration_count", 0)

    if should_retry and iteration <= _MAX_RETRY_ITERATIONS:
        logger.info("edge.retry_retrieval", iteration=iteration)
        return "retrieval"

    department = (state.get("target_department") or "general").lower()
    valid_departments = {"hr", "finance", "sales", "marketing", "general"}
    if department not in valid_departments:
        department = "general"

    logger.debug("edge.route_to_department", department=department)
    return department


def route_after_department_agent(state: AgentState) -> str:
    """
    After department agent reasoning, decide whether to generate a document
    or synthesize the response.

    Returns: "doc_generation" | "response_synthesizer"
    """
    if state.get("requires_document_generation") and state.get("document_output"):
        doc_spec = state.get("document_output", {})
        # Only generate if we have a complete spec
        if doc_spec.get("spec") and doc_spec["spec"].get("title"):
            logger.debug("edge.route_to_doc_generation")
            return "doc_generation"

    logger.debug("edge.route_to_response_synthesizer")
    return "response_synthesizer"


def route_after_doc_generation(state: AgentState) -> str:
    """
    After document generation, always proceed to response synthesis
    (to include the file in the final response).

    Returns: "response_synthesizer"
    """
    return "response_synthesizer"
