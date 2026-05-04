"""LangGraph node — self-critique and quality validation of the generated answer."""

from __future__ import annotations

import json

import structlog

from config.settings import get_settings
from src.models.state import AgentState

logger = structlog.get_logger()


async def quality_check_node(state: AgentState) -> dict:
    """
    Self-critique node — validates the generated answer for:
    1. Faithfulness (grounded in retrieved context)
    2. Completeness (addresses all parts of the query)
    3. Citation quality (sources are properly cited)

    If quality is low, sets should_retry_retrieval=True for one retry.
    """
    answer = state.get("answer", "")
    query = state.get("original_query", "")
    results = state.get("reranked_results") or state.get("raw_results", [])
    iteration = state.get("iteration_count", 0)

    # Skip quality check on retry iterations to avoid loops
    if iteration > 0:
        return {"should_retry_retrieval": False}

    if not answer or not query:
        return {"should_retry_retrieval": False}

    settings = get_settings()

    try:
        from langchain_openai import AzureChatOpenAI

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.LLM_CHEAP_MODEL,
            max_tokens=200,
        )

        # Build a compact context summary
        context_summary = "\n".join(
            f"- {r.get('document_title', 'doc')}: {r.get('content', '')[:200]}"
            for r in results[:5]
        )

        prompt = f"""Evaluate this RAG answer. Return JSON with:
- "faithful": true if answer is grounded in the context (no hallucination)
- "complete": true if the query is fully addressed
- "cited": true if sources are properly referenced
- "score": 1-10 overall quality
- "issue": brief description of any problem (or null)

Query: {query}
Context (summary): {context_summary}
Answer: {answer[:2000]}

Return ONLY valid JSON."""

        response = await llm.ainvoke(prompt)
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        evaluation = json.loads(content)

        score = evaluation.get("score", 7)
        should_retry = score < 5 and not evaluation.get("faithful", True)

        logger.info(
            "node.quality_check",
            score=score,
            faithful=evaluation.get("faithful"),
            complete=evaluation.get("complete"),
            should_retry=should_retry,
        )

        return {
            "should_retry_retrieval": should_retry,
            "iteration_count": iteration + 1,
        }

    except Exception as e:
        logger.warn("quality_check.failed", error=str(e))
        return {"should_retry_retrieval": False, "iteration_count": iteration + 1}
