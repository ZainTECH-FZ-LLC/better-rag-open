"""LangGraph StateGraph orchestrator — wires all nodes into the RAG pipeline."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from src.graph.nodes.answer_generation import answer_generation_node
from src.graph.nodes.doc_generation import doc_generation_node
from src.graph.nodes.quality_check import quality_check_node
from src.graph.nodes.query_analysis import query_analysis_node
from src.graph.nodes.retrieval import retrieval_node
from src.graph.nodes.smart_router import smart_router_node
from src.models.state import AgentState

logger = structlog.get_logger()


def _should_retry(state: AgentState) -> str:
    """Conditional edge: retry retrieval or proceed to output."""
    if state.get("should_retry_retrieval"):
        return "retrieval"
    return "output"


def _should_generate_doc(state: AgentState) -> str:
    """Conditional edge: route to doc generation or smart router."""
    if state.get("requires_document_generation"):
        return "doc_generation"
    return "smart_router"


def _route_after_smart_router(state: AgentState) -> str:
    """Conditional edge: skip retrieval for smalltalk, else proceed."""
    if state.get("is_smalltalk"):
        return "answer_generation"
    return "retrieval"


def build_graph() -> StateGraph:
    """Build the LangGraph StateGraph for the RAG pipeline."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("query_analysis", query_analysis_node)
    graph.add_node("smart_router", smart_router_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("answer_generation", answer_generation_node)
    graph.add_node("quality_check", quality_check_node)
    graph.add_node("doc_generation", doc_generation_node)

    # Set entry point
    graph.set_entry_point("query_analysis")

    # Edges
    graph.add_conditional_edges(
        "query_analysis",
        _should_generate_doc,
        {
            "smart_router": "smart_router",
            "doc_generation": "doc_generation",
        },
    )

    graph.add_conditional_edges(
        "smart_router",
        _route_after_smart_router,
        {
            "retrieval": "retrieval",
            "answer_generation": "answer_generation",
        },
    )

    graph.add_edge("retrieval", "answer_generation")
    graph.add_edge("answer_generation", "quality_check")

    graph.add_conditional_edges(
        "quality_check",
        _should_retry,
        {
            "retrieval": "retrieval",
            "output": END,
        },
    )

    graph.add_edge("doc_generation", END)

    return graph


# Compiled graph singleton
_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph().compile()
    return _compiled_graph


async def run_orchestrator(
    messages: list[dict[str, Any]],
    user_id: str = "anonymous",  # reserved for future per-user RBAC
    user_email: str = "",
    document_type: str | None = None,
    retrieval_strategy_override: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Stream SSE events by running orchestrator nodes then streaming the LLM answer.

    Pipeline:
      1. query_analysis_node  — intent, reformulation, department routing
      2. retrieval_node       — RetrievalPipeline (vector + graph + rerank)
      3. LLM streaming        — AsyncAzureOpenAI stream=True with retrieved context
      4. citations            — emitted after the answer stream closes

    SSE event types:
    - {"type": "status",   "message": "...", "done": bool}
    - {"type": "token",    "content": "..."}
    - {"type": "citation", "title": "...", ...}
    - {"type": "error",    "message": "..."}
    """
    from config.settings import get_settings
    from openai import AsyncAzureOpenAI

    yield {"type": "status", "message": "Analyzing query…", "done": False}

    # Build initial state — keep recent conversation history for context
    lc_messages = []
    for msg in messages:
        if not isinstance(msg, dict) or not msg.get("content"):
            continue
        if msg.get("role") == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg.get("role") == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    state: AgentState = {
        "messages": lc_messages,
        "user_context": {
            # Use "anonymous" for RBAC until per-user access control is set up.
            # Documents are ingested with document_user_access = "anonymous".
            # Switch to user_id once per-user RBAC is configured.
            "user_id": "anonymous",
            "user_email": user_email,
            "department": None,
            "roles": [],
            "access_level": None,
        },
        "original_query": "",
        "iteration_count": 0,
        "should_retry_retrieval": False,
    }
    if document_type:
        state["document_output"] = {"doc_type": document_type, "template_name": None, "spec": {}}
        state["requires_document_generation"] = True
    if retrieval_strategy_override:
        state["retrieval_strategy"] = retrieval_strategy_override

    try:
        # ── Step 1: Query analysis node ────────────────────────────────────────
        analysis = await query_analysis_node(state)
        state = {**state, **analysis}

        if not state.get("original_query"):
            yield {"type": "error", "message": "No user message found."}
            return

        logger.info(
            "orchestrator.query_analysis",
            department=state.get("target_department"),
            strategy=state.get("retrieval_strategy"),
            reformulated=state.get("reformulated_query"),
        )

        # ── Step 2: Retrieval node ─────────────────────────────────────────────
        yield {"type": "status", "message": "Searching knowledge base…", "done": False}

        retrieval = await retrieval_node(state)
        state = {**state, **retrieval}

        reranked = state.get("reranked_results") or state.get("raw_results", [])
        graph_chunks = state.get("graph_chunks", [])
        graph_context = state.get("graph_context", [])
        all_chunks = reranked + graph_chunks

        logger.info(
            "orchestrator.retrieval",
            reranked=len(reranked),
            graph_chunks=len(graph_chunks),
        )

        if not all_chunks:
            yield {"type": "token", "content": "I couldn't find relevant documents for your query."}
            yield {"type": "status", "message": "Done", "done": True}
            return

        # ── Step 3: Stream LLM answer with retrieved context ───────────────────
        yield {"type": "status", "message": "Generating answer…", "done": False}

        context_parts = []
        for i, chunk in enumerate(all_chunks, 1):
            title = chunk.get("document_title") or "Unknown"
            heading = chunk.get("section_heading") or ""
            header = f"[{i}] {title}" + (f" — {heading}" if heading else "")
            context_parts.append(f"{header}\n{chunk.get('content_with_context') or chunk.get('content', '')}")

        if graph_context:
            related_parts = []
            for ctx in graph_context[:3]:
                title = ctx.get("title", "")
                summary = ctx.get("summary", "")
                rel_types = ctx.get("relationship_types") or []
                if title and summary:
                    rel_label = f" ({', '.join(rel_types)})" if rel_types else ""
                    related_parts.append(f"[Related{rel_label}] {title}\n{summary[:300]}")
            if related_parts:
                context_parts.append(
                    "--- Related Documents (via knowledge graph) ---\n"
                    + "\n\n".join(related_parts)
                )

        context = "\n\n---\n\n".join(context_parts)
        system_prompt = (
            "You are a helpful enterprise assistant. Answer the user's question based only on the "
            "provided context. Cite sources by their [number]. If the context doesn't contain enough "
            "information, say so clearly."
        )
        user_message = f"Context:\n{context}\n\nQuestion: {state['original_query']}"

        # Build chat history from prior turns (last 10 turns max to stay within token budget)
        history_messages: list[dict[str, str]] = []
        prior_turns = [
            m for m in lc_messages[:-1]  # everything except the current query
        ]
        for msg in prior_turns[-10:]:
            if isinstance(msg, HumanMessage):
                history_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                history_messages.append({"role": "assistant", "content": msg.content})

        settings = get_settings()
        client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        llm_stream = await client.chat.completions.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                *history_messages,
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=16384,
            stream=True,
        )
        async for chunk in llm_stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield {"type": "token", "content": delta.content}

        # ── Step 4: Citations ──────────────────────────────────────────────────
        citations = state.get("citations", [])
        seen: set[str] = set()
        for citation in citations:
            title = citation.get("document_title") or "Unknown"
            if title not in seen:
                seen.add(title)
                yield {
                    "type": "citation",
                    "title": title,
                    "content": "",
                    "sharepoint_url": citation.get("sharepoint_url", ""),
                    "department": citation.get("department", ""),
                    "section": citation.get("section_heading", ""),
                    "file_type": "",
                    "pages": citation.get("page_numbers") or [],
                }

    except Exception as e:
        logger.error("orchestrator.failed", error=str(e))
        yield {"type": "error", "message": f"Pipeline error: {str(e)}"}

    yield {"type": "status", "message": "Done", "done": True}


