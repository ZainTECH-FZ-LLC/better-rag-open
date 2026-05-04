"""LangGraph node — final answer assembly with citations."""

from __future__ import annotations

import structlog

from config.settings import get_settings
from src.agents.prompts.system_prompts import DEPARTMENT_PROMPTS
from src.models.state import AgentState

logger = structlog.get_logger()

_MAX_CONTEXT_CHARS = 12_000  # ~3K tokens of context


async def response_synthesizer_node(state: AgentState) -> dict:
    """
    Assemble the final answer using the department-specific prompt and retrieved context.

    This is the only node that uses the expensive LLM model.
    The department agent has already done reasoning; this node synthesizes the final
    user-facing response with proper citations and formatting.

    Updates state with:
    - answer: final text response
    - citations: list of citation dicts (document_id, title, url, etc.)
    - answer_tokens: tokenized answer for streaming
    """
    settings = get_settings()
    query = state.get("original_query", "")
    department = (state.get("target_department") or "general").lower()
    reranked = state.get("reranked_results") or state.get("raw_results") or []
    graph_chunks = state.get("graph_chunks") or []
    graph_context = state.get("graph_context") or []

    # Merge reranked results + graph chunks for full context
    reranked = reranked + graph_chunks
    generated_file = state.get("generated_file")

    # Build context block
    context_parts = []
    for i, chunk in enumerate(reranked[:8]):
        content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
        title = chunk.get("document_title") if isinstance(chunk, dict) else getattr(chunk, "document_title", "")
        url = chunk.get("sharepoint_url") if isinstance(chunk, dict) else getattr(chunk, "sharepoint_url", "")
        heading = chunk.get("section_heading") if isinstance(chunk, dict) else getattr(chunk, "section_heading", None)

        source_ref = f"[{title}]({url})" if url else title
        section = f" — {heading}" if heading else ""
        context_parts.append(f"**Source {i + 1}**: {source_ref}{section}\n{content[:800]}")

    # Add graph context (related documents)
    if graph_context:
        related_parts = []
        for ctx in graph_context[:3]:
            title = ctx.get("title", "")
            summary = ctx.get("summary", "")
            url = ctx.get("sharepoint_url", "")
            if title and summary:
                ref = f"[{title}]({url})" if url else title
                related_parts.append(f"**Related**: {ref}\n{summary[:300]}")
        if related_parts:
            context_parts.append("\n**Related Documents (via knowledge graph):**\n" + "\n\n".join(related_parts))

    context_text = "\n\n---\n\n".join(context_parts)
    if len(context_text) > _MAX_CONTEXT_CHARS:
        context_text = context_text[:_MAX_CONTEXT_CHARS] + "\n\n[Context truncated for length]"

    # Add note about generated file if applicable
    file_note = ""
    if generated_file:
        filename = generated_file.get("filename", "document")
        file_note = f"\n\nNote: A {filename} has been generated and is attached below."

    system_prompt = DEPARTMENT_PROMPTS.get(department, DEPARTMENT_PROMPTS["general"])
    user_message = f"""Question: {query}

Retrieved Context:
{context_text}

Answer the question using the retrieved context. Cite sources inline using [Title](URL) format.
Be specific and accurate. Format your response in clear Markdown.{file_note}"""

    try:
        answer = await _call_expensive_llm(system_prompt, user_message, settings)
    except Exception as e:
        logger.error("node.response_synthesizer.llm_failed", error=str(e))
        answer = _fallback_answer(query, reranked)

    # Build citations from reranked results
    citations = _build_citations(reranked)

    logger.info(
        "node.response_synthesizer",
        department=department,
        answer_length=len(answer),
        citation_count=len(citations),
    )

    return {
        "answer": answer,
        "citations": citations,
        "answer_tokens": answer.split(" "),  # For SSE streaming
    }


async def _call_expensive_llm(system_prompt: str, user_message: str, settings) -> str:
    """Call the expensive/capable LLM model for final answer generation."""
    if settings.LLM_PROVIDER.value == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            max_completion_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    else:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = client.chat.completions.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=2048,
        )
        return response.choices[0].message.content or ""


def _build_citations(reranked: list) -> list[dict]:
    """Build deduplicated citation list from reranked chunks."""
    seen_docs: set[str] = set()
    citations: list[dict] = []

    for chunk in reranked:
        doc_id = chunk.get("document_id") if isinstance(chunk, dict) else getattr(chunk, "document_id", "")
        if not doc_id or doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)

        title = chunk.get("document_title") if isinstance(chunk, dict) else getattr(chunk, "document_title", "")
        url = chunk.get("sharepoint_url") if isinstance(chunk, dict) else getattr(chunk, "sharepoint_url", "")
        content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
        score = chunk.get("score") if isinstance(chunk, dict) else getattr(chunk, "score", 0.0)

        citations.append({
            "document_id": doc_id,
            "document_title": title,
            "sharepoint_url": url,
            "content": content[:500] if content else "",
            "score": float(score) if score else 0.0,
        })

    return citations


def _fallback_answer(query: str, reranked: list) -> str:
    """Generate a basic answer from raw context when LLM call fails."""
    if not reranked:
        return "I was unable to find relevant information to answer your question."

    parts = [f"Based on retrieved documents for: **{query}**\n"]
    for chunk in reranked[:3]:
        content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
        title = chunk.get("document_title") if isinstance(chunk, dict) else getattr(chunk, "document_title", "")
        if content:
            parts.append(f"**{title}:**\n{content[:400]}")

    return "\n\n".join(parts)
