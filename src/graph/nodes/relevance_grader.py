"""LangGraph node — self-reflection relevance grader for retrieval quality."""

from __future__ import annotations

import structlog

from config.settings import get_settings
from src.models.state import AgentState

logger = structlog.get_logger()

_GRADE_PROMPT = """You are evaluating whether retrieved document chunks are relevant to a user's question.

Question: {query}

Retrieved chunks (top 3):
{chunks}

For each chunk, answer YES if it contains information useful for answering the question, NO otherwise.
Then give an overall verdict: SUFFICIENT (2+ relevant chunks) or INSUFFICIENT.

Respond with ONLY this format:
Chunk 1: YES/NO
Chunk 2: YES/NO
Chunk 3: YES/NO
Overall: SUFFICIENT/INSUFFICIENT"""

_MIN_RELEVANT_CHUNKS = 2


async def relevance_grader_node(state: AgentState) -> dict:
    """
    Grade the retrieval quality using a cheap LLM.

    Evaluates the top 3 reranked results against the original query.
    If fewer than 2 chunks are relevant, sets should_retry_retrieval=True
    (triggering a HyDE reformulation retry via edge routing).

    Updates state with:
    - should_retry_retrieval: whether to retry with HyDE
    - iteration_count: incremented
    """
    settings = get_settings()
    query = state.get("original_query", "")
    reranked = state.get("reranked_results") or state.get("raw_results") or []
    iteration = state.get("iteration_count", 0)

    # If no results at all, always retry (once)
    if not reranked:
        logger.info("node.relevance_grader", verdict="INSUFFICIENT", reason="no_results")
        return {
            "should_retry_retrieval": True,
            "iteration_count": iteration + 1,
        }

    # Sample top 3 for grading
    top_chunks = reranked[:3]
    chunks_text = "\n\n".join(
        f"Chunk {i + 1}: {c.get('content', c) if isinstance(c, dict) else getattr(c, 'content', str(c))[:400]}"
        for i, c in enumerate(top_chunks)
    )

    prompt = _GRADE_PROMPT.format(query=query, chunks=chunks_text)

    try:
        verdict, relevant_count = await _grade_with_llm(prompt, settings)
    except Exception as e:
        logger.warn("node.relevance_grader.llm_failed", error=str(e))
        # On LLM failure, assume sufficient to avoid loops
        return {"should_retry_retrieval": False, "iteration_count": iteration + 1}

    should_retry = (
        verdict == "INSUFFICIENT"
        and relevant_count < _MIN_RELEVANT_CHUNKS
        and iteration == 0  # Only retry once
    )

    # Force retry to use HyDE on next pass
    if should_retry:
        current_strategy = state.get("retrieval_strategy", "cosine")
        if "hyde" not in current_strategy:
            return {
                "should_retry_retrieval": True,
                "retrieval_strategy": "hyde_cosine",
                "iteration_count": iteration + 1,
            }

    logger.info(
        "node.relevance_grader",
        verdict=verdict,
        relevant_count=relevant_count,
        should_retry=should_retry,
        iteration=iteration,
    )

    return {
        "should_retry_retrieval": should_retry,
        "iteration_count": iteration + 1,
    }


async def _grade_with_llm(prompt: str, settings) -> tuple[str, int]:
    """Call cheap LLM to grade relevance. Returns (verdict, relevant_count)."""
    if settings.LLM_PROVIDER.value == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.LLM_CHEAP_MODEL,
            max_completion_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
    else:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = client.chat.completions.create(
            model=settings.LLM_CHEAP_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=100,
        )
        text = response.choices[0].message.content or ""

    # Parse response
    lines = text.strip().split("\n")
    relevant_count = sum(
        1 for line in lines
        if line.startswith("Chunk") and "YES" in line.upper()
    )
    verdict = "SUFFICIENT"
    for line in lines:
        if line.startswith("Overall:"):
            verdict = "INSUFFICIENT" if "INSUFFICIENT" in line.upper() else "SUFFICIENT"
            break

    return verdict, relevant_count
