"""Query analyzer — classifies intent, extracts metadata filters, selects retrieval strategy."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import structlog

from config.settings import get_settings
from src.storage.cache import cache_query_analysis, get_cached_query_analysis

logger = structlog.get_logger()

# Keywords that indicate a query needs LLM analysis (multi-doc synthesis,
# generation intent, or vague analytical requests the rule-based router
# cannot reliably classify).
_COMPLEX_INDICATORS = frozenset({
    "compare", "contrast", "analyze", "analysis", "across",
    "trend", "throughout", "synthesize", "generate", "create",
    "draft", "write", "build",
})


def _is_complex(query: str) -> bool:
    """Return True if the query needs LLM routing; False if rule-based is sufficient."""
    q = query.lower()
    return any(kw in q for kw in _COMPLEX_INDICATORS) or len(query.split()) > 15


def _analysis_to_dict(a: QueryAnalysis) -> dict:
    return asdict(a)


def _dict_to_analysis(d: dict) -> QueryAnalysis:
    return QueryAnalysis(**d)


@dataclass
class QueryAnalysis:
    """Structured output from query analysis."""

    query_type: str = "factual"  # factual, analytical, procedural, generative, smalltalk
    target_department: str | None = None
    retrieval_strategy: str = "cosine"  # cosine, hyde_cosine, mmr
    metadata_filters: dict = field(default_factory=dict)
    requires_document_generation: bool = False
    document_type: str | None = None  # pptx, docx, xlsx
    reformulated_query: str | None = None
    keywords: list[str] = field(default_factory=list)


class QueryAnalyzer:
    """
    LLM-based query analysis via smart router.

    Determines:
    1. Query type (factual/analytical/procedural/generative)
    2. Target department for routing to sub-agents
    3. Optimal retrieval strategy (plain cosine vs HyDE vs MMR)
    4. Metadata pre-filters (department, content type, date range)
    5. Whether document generation is needed

    Optimisations:
    - Simple factual queries bypass the LLM and use rule-based analysis directly.
    - LLM analysis results are cached in Redis (CACHE_QUERY_TTL).
    - The AzureChatOpenAI client is instantiated once and reused across calls.
    """

    _llm = None  # shared across all instances; instantiated on first complex query

    def __init__(self) -> None:
        self.settings = get_settings()

    def _get_llm(self):
        if QueryAnalyzer._llm is None:
            from langchain_openai import AzureChatOpenAI
            QueryAnalyzer._llm = AzureChatOpenAI(
                azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
                api_key=self.settings.AZURE_OPENAI_API_KEY,
                api_version=self.settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=self.settings.LLM_CHEAP_MODEL,
                max_tokens=300,
            )
        return QueryAnalyzer._llm

    async def analyze(
        self,
        query: str,
        user_department: str | None = None,
        chat_history: str | None = None,
    ) -> QueryAnalysis:
        """Analyze a query and return structured routing decisions."""
        has_history = bool(chat_history)

        # Simple queries with no prior conversation: skip LLM entirely
        if not _is_complex(query) and not has_history:
            logger.debug("query_analyzer.rule_based", query=query[:80])
            return self._fallback_analyze(query)

        # Check Redis cache (skip when history exists — result depends on context)
        if not has_history:
            try:
                cached = await get_cached_query_analysis(query, user_department)
                if cached:
                    logger.debug("query_analyzer.cache_hit", query=query[:80])
                    return _dict_to_analysis(cached)
            except Exception:
                pass  # Cache unavailable — proceed without it

        try:
            result = await self._llm_analyze(query, user_department, chat_history)
        except Exception as e:
            logger.warn("query_analyzer.llm_failed", error=str(e))
            result = self._fallback_analyze(query)

        if not has_history:
            try:
                await cache_query_analysis(query, user_department, _analysis_to_dict(result))
            except Exception:
                pass  # Cache write failure is non-fatal

        return result

    async def _llm_analyze(
        self,
        query: str,
        user_department: str | None,
        chat_history: str | None = None,
    ) -> QueryAnalysis:
        llm = self._get_llm()

        history_block = ""
        if chat_history:
            history_block = f"""
Recent conversation history (use this to resolve references like "that", "it", "more about", etc.):
---
{chat_history}
---

"""

        prompt = f"""Analyze this user query for an enterprise RAG system. Return JSON with:

- "query_type": "factual" (specific fact/data lookup), "analytical" (requires synthesis across docs), "procedural" (step-by-step how-to), "generative" (create a document), or "smalltalk" (greetings, thanks, bye, chitchat — NOT a knowledge question)
- "target_department": "hr", "finance", "sales", "marketing", or "general"
- "retrieval_strategy": "cosine" (simple factual lookup), "hyde_cosine" (analytical/vague queries), or "mmr" (broad topics needing diverse results)
- "metadata_filters": object with optional "department", "content_type" fields to narrow search
- "requires_document_generation": true if user wants to create/generate a document (pptx, docx, xlsx)
- "document_type": "pptx", "docx", or "xlsx" if generation is needed, else null
- "reformulated_query": improved version of the query for retrieval. IMPORTANT: if this is a follow-up question, rewrite it as a standalone query by resolving references from the conversation history.
- "keywords": list of 3-5 key search terms

{history_block}User's department: {user_department or "unknown"}
Query: {query}

Return ONLY valid JSON."""

        response = await llm.ainvoke(prompt)
        content = response.content.strip()

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        data = json.loads(content)

        return QueryAnalysis(
            query_type=data.get("query_type", "factual"),
            target_department=data.get("target_department"),
            retrieval_strategy=data.get("retrieval_strategy", "cosine"),
            metadata_filters=data.get("metadata_filters", {}),
            requires_document_generation=data.get("requires_document_generation", False),
            document_type=data.get("document_type"),
            reformulated_query=data.get("reformulated_query"),
            keywords=data.get("keywords", []),
        )

    _SMALLTALK_PHRASES = frozenset({
        "hi", "hello", "hey", "howdy", "hola", "greetings", "yo", "sup",
        "good morning", "good afternoon", "good evening", "good day",
        "how are you", "hows it going", "how do you do", "whats up",
        "thanks", "thank you", "thank you so much", "thx", "ty", "cheers",
        "bye", "goodbye", "see you", "take care", "later", "cya",
        "have a good day", "have a nice day",
        "ok", "okay", "alright", "sure", "got it", "understood",
        "cool", "nice", "great", "awesome",
        "who are you", "what are you", "what can you do",
        "what do you do", "help",
    })

    @staticmethod
    def _is_smalltalk(query: str) -> bool:
        """Check if query is smalltalk/greeting — short, non-informational."""
        q = query.strip().lower().rstrip("!?.,:; ")
        return q in QueryAnalyzer._SMALLTALK_PHRASES

    def _fallback_analyze(self, query: str) -> QueryAnalysis:
        """Rule-based fallback if LLM analysis fails."""
        query_lower = query.lower()
        analysis = QueryAnalysis()

        # Smalltalk — must check before other query types
        if self._is_smalltalk(query):
            analysis.query_type = "smalltalk"
            return analysis

        # Query type
        if any(kw in query_lower for kw in ["create", "generate", "make", "build"]):
            analysis.query_type = "generative"
        elif any(kw in query_lower for kw in ["how to", "steps", "process", "procedure"]):
            analysis.query_type = "procedural"
        elif any(kw in query_lower for kw in ["compare", "analyze", "trend", "summary"]):
            analysis.query_type = "analytical"
        else:
            analysis.query_type = "factual"

        # Department — use word-boundary matching to avoid false positives
        # (e.g. "bahrain" should NOT match "hr")
        dept_keywords = {
            "hr": ["hr", "leave", "policy", "onboarding", "benefit", "salary"],
            "finance": ["budget", "revenue", "financial", "expense", "forecast"],
            "sales": ["pipeline", "deal", "quota", "client", "prospect"],
            "marketing": ["campaign", "brand", "content", "social", "launch"],
        }
        for dept, keywords in dept_keywords.items():
            if any(re.search(rf"\b{re.escape(kw)}\b", query_lower) for kw in keywords):
                analysis.target_department = dept
                break

        # Strategy
        if analysis.query_type == "analytical":
            analysis.retrieval_strategy = "hyde_cosine"
        elif analysis.query_type == "procedural":
            analysis.retrieval_strategy = "mmr"
        else:
            analysis.retrieval_strategy = "cosine"

        # Document generation
        if analysis.query_type == "generative":
            analysis.requires_document_generation = True
            if "pptx" in query_lower or "presentation" in query_lower or "slide" in query_lower:
                analysis.document_type = "pptx"
            elif "xlsx" in query_lower or "spreadsheet" in query_lower:
                analysis.document_type = "xlsx"
            elif "docx" in query_lower or "document" in query_lower or "report" in query_lower:
                analysis.document_type = "docx"

        return analysis
