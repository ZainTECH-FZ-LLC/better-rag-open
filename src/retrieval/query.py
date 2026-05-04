"""Single-function entry point for the retrieval pipeline + answer generation.

Usage in an API endpoint::

    from src.retrieval.query import query_rag

    result = await query_rag("What is Zain's strategy?")
    # result.answer, result.citations, result.chunks, ...
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from openai import AsyncAzureOpenAI

from config.settings import get_settings
from src.retrieval.pipeline import RetrievalPipeline, RetrievalResult
from src.storage.db import get_db_session

logger = structlog.get_logger()


@dataclass
class QueryResponse:
    """Structured response from the RAG pipeline."""

    answer: str
    citations: list[dict] = field(default_factory=list)
    chunk_dicts: list[dict] = field(default_factory=list)
    chunks_used: int = 0
    query_analysis: dict = field(default_factory=dict)
    graph_context: list[dict] = field(default_factory=list)


async def _generate_answer(
    query: str,
    chunks: list[dict],
    graph_context: list[dict] | None = None,
) -> str:
    """Generate an LLM answer from retrieved chunks."""
    settings = get_settings()

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("document_title") or "Unknown"
        heading = chunk.get("section_heading") or ""
        header = f"[{i}] {title}" + (f" — {heading}" if heading else "")
        context_parts.append(f"{header}\n{chunk['content_with_context']}")

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
    user_message = f"Context:\n{context}\n\nQuestion: {query}"

    client = AsyncAzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )
    response = await client.chat.completions.create(
        model=settings.LLM_EXPENSIVE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_completion_tokens=16384,
    )
    return response.choices[0].message.content or ""


def _chunks_to_dicts(result: RetrievalResult) -> list[dict]:
    """Convert ChunkResult objects from the pipeline into plain dicts."""
    chunk_dicts = []
    for chunk in result.all_chunks:
        chunk_dicts.append({
            "content": chunk.content,
            "content_with_context": chunk.content_with_context,
            "document_title": chunk.document_title,
            "section_heading": chunk.section_heading,
            "page_numbers": chunk.page_numbers,
            "document_id": chunk.document_id,
            "source": chunk.sharepoint_url,
        })
    return chunk_dicts


async def query_rag(
    query: str,
    *,
    top_k: int = 10,
    user_id: str = "anonymous",
    user_department: str | None = None,
    skip_answer: bool = False,
) -> QueryResponse:
    """Run the full retrieval pipeline and return a structured response.

    Args:
        query: User's natural language question.
        top_k: Number of final chunks to retrieve.
        user_id: User ID for RBAC filtering.
        user_department: Optional department for routing.
        skip_answer: If True, skip LLM answer generation (return chunks only).

    Returns:
        QueryResponse with answer, citations, and metadata.
    """
    async with get_db_session() as db:
        pipeline = RetrievalPipeline(db_session=db)
        result = await pipeline.retrieve(
            query=query,
            user_id=user_id,
            user_department=user_department,
            k=top_k,
            fetch_k=top_k * 4,
        )

    chunk_dicts = _chunks_to_dicts(result)

    answer = ""
    if not skip_answer and chunk_dicts:
        answer = await _generate_answer(query, chunk_dicts, graph_context=result.graph_context)

    return QueryResponse(
        answer=answer,
        citations=result.citations,
        chunk_dicts=chunk_dicts,
        chunks_used=len(chunk_dicts),
        query_analysis={
            "query_type": result.query_analysis.query_type,
            "retrieval_strategy": result.query_analysis.retrieval_strategy,
            "target_department": result.query_analysis.target_department,
            "reformulated_query": result.query_analysis.reformulated_query,
        },
        graph_context=result.graph_context,
    )
