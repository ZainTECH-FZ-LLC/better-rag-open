"""HyDE (Hypothetical Document Embedding) — generates a hypothetical answer for better retrieval."""

from __future__ import annotations

import structlog

from config.settings import get_settings
from src.embedding.azure_openai import AzureOpenAIEmbedder
from src.storage.cache import cache_hyde_embedding, get_cached_hyde_embedding

logger = structlog.get_logger()


class HyDEGenerator:
    """
    Generates a hypothetical document that would answer the user's query,
    then embeds that document for retrieval instead of the raw query.

    This bridges the semantic gap between short questions and long passages.

    The AzureChatOpenAI client is instantiated once and reused across calls.
    """

    _llm = None  # shared across all instances; instantiated on first call

    def __init__(self, embedder: AzureOpenAIEmbedder | None = None) -> None:
        self.settings = get_settings()
        self._embedder = embedder

    def _get_llm(self):
        if HyDEGenerator._llm is None:
            from langchain_openai import AzureChatOpenAI
            HyDEGenerator._llm = AzureChatOpenAI(
                azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
                api_key=self.settings.AZURE_OPENAI_API_KEY,
                api_version=self.settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=self.settings.LLM_CHEAP_MODEL,
                max_tokens=300,
            )
        return HyDEGenerator._llm

    async def _get_embedder(self) -> AzureOpenAIEmbedder:
        if self._embedder is None:
            self._embedder = AzureOpenAIEmbedder()
        return self._embedder

    async def generate_embedding(self, query: str) -> list[float]:
        """
        Generate a HyDE embedding for a query.

        1. Check Redis cache
        2. If miss: generate hypothetical document via LLM
        3. Embed the hypothetical document
        4. Cache the result
        """
        cached = await get_cached_hyde_embedding(query)
        if cached:
            logger.debug("hyde.cache_hit", query=query[:80])
            return cached

        hypo_doc = await self._generate_hypothetical(query)

        embedder = await self._get_embedder()
        embedding = await embedder.embed_query(hypo_doc)

        await cache_hyde_embedding(query, embedding)

        logger.info(
            "hyde.generated",
            query=query[:80],
            hypo_length=len(hypo_doc),
        )
        return embedding

    async def _generate_hypothetical(self, query: str) -> str:
        """Generate a hypothetical document passage that would answer the query."""
        llm = self._get_llm()

        prompt = f"""Write a short passage (150-250 words) from an internal company document
that would directly answer the following question. Write it as if it were
an excerpt from an actual policy document, report, or memo. Include specific
details and use a professional tone.

Question: {query}

Document excerpt:"""

        response = await llm.ainvoke(prompt)
        return response.content.strip()
