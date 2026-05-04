"""Cohere reranker via Azure AI Foundry — API-based re-scoring of candidate chunks."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from config.settings import get_settings
from src.storage.vector_store import ChunkResult

logger = structlog.get_logger()


@dataclass
class RerankedResult:
    """A reranked search result."""

    chunk: ChunkResult
    rerank_score: float
    original_rank: int


class CohereReranker:
    """
    Reranker using Cohere Rerank via Azure AI Foundry.

    Uses direct REST calls because the Cohere SDK's URL construction
    is incompatible with Azure AI Services' /providers/cohere/v2 path.

    Flow: Vector search (top-60) → Cohere Rerank → top-k results
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._endpoint = self.settings.COHERE_AZURE_ENDPOINT.rstrip("/")
        self._api_key = self.settings.COHERE_AZURE_API_KEY
        self._client = httpx.AsyncClient(timeout=30)

    async def rerank(
        self,
        query: str,
        candidates: list[ChunkResult],
        top_k: int = 10,
    ) -> list[RerankedResult]:
        """
        Rerank candidates using the Cohere Rerank API.

        Args:
            query: The user's query text.
            candidates: Candidate chunks from vector search.
            top_k: Number of results to return after reranking.

        Returns:
            Top-k results sorted by relevance score descending.
        """
        if not candidates:
            return []

        documents = [c.content_with_context for c in candidates]

        response = await self._client.post(
            f"{self._endpoint}/rerank",
            json={
                "model": self.settings.COHERE_RERANK_MODEL,
                "query": query,
                "documents": documents,
                "top_n": top_k,
            },
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

        results = [
            RerankedResult(
                chunk=candidates[hit["index"]],
                rerank_score=hit["relevance_score"],
                original_rank=hit["index"],
            )
            for hit in data["results"]
        ]

        logger.info(
            "reranker.completed",
            candidates=len(candidates),
            returned=len(results),
            top_score=results[0].rerank_score if results else 0,
        )

        return results
