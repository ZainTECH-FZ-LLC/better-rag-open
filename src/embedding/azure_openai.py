"""Azure OpenAI embedding provider with batch manager and rate limiting."""

from __future__ import annotations

import asyncio

import structlog
import tiktoken
from openai import AsyncAzureOpenAI

from config.settings import get_settings
from src.embedding.base import EmbeddingProvider

logger = structlog.get_logger()

# text-embedding-3-large hard limit; stay 192 tokens under the cap for safety
_EMBEDDING_MAX_TOKENS = 8000
_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def _truncate_to_token_limit(text: str, max_tokens: int = _EMBEDDING_MAX_TOKENS) -> str:
    """Truncate text so it fits within the embedding model's token limit."""
    tokens = _TOKENIZER.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated = _TOKENIZER.decode(tokens[:max_tokens])
    logger.warning(
        "embedder.truncated",
        original_tokens=len(tokens),
        max_tokens=max_tokens,
        chars_before=len(text),
        chars_after=len(truncated),
    )
    return truncated


class AzureOpenAIEmbedder(EmbeddingProvider):
    """
    Embedding provider using Azure OpenAI text-embedding-3-large.

    Features:
    - Automatic batching (configurable batch size, default 16)
    - Exponential backoff on rate limit errors
    - Configurable dimensions (Matryoshka property)
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = AsyncAzureOpenAI(
            azure_endpoint=self.settings.EMBEDDING_AZURE_ENDPOINT,
            api_key=self.settings.EMBEDDING_AZURE_API_KEY,
            api_version=self.settings.EMBEDDING_AZURE_API_VERSION,
        )
        self._deployment = self.settings.EMBEDDING_AZURE_DEPLOYMENT
        self._dimensions = self.settings.EMBEDDING_DIMENSIONS
        self._batch_size = self.settings.EMBEDDING_BATCH_SIZE

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        response = await self._call_api([text])
        return response[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts with automatic batching.

        Splits into batches of `batch_size`, fires all batches concurrently
        (up to 4 in parallel), and concatenates results in order.
        """
        if not texts:
            return []

        batches = [
            texts[i : i + self._batch_size]
            for i in range(0, len(texts), self._batch_size)
        ]

        # Run up to 4 embedding batches concurrently
        semaphore = asyncio.Semaphore(4)

        async def _embed_batch(batch_idx: int, batch: list[str]):
            async with semaphore:
                result = await self._call_api(batch)
                logger.debug(
                    "embedder.batch_complete",
                    batch_idx=batch_idx,
                    batch_size=len(batch),
                    total=len(texts),
                )
                return result

        batch_results = await asyncio.gather(
            *(_embed_batch(i, b) for i, b in enumerate(batches))
        )

        all_embeddings: list[list[float]] = []
        for br in batch_results:
            all_embeddings.extend(br)

        return all_embeddings

    async def _call_api(
        self,
        texts: list[str],
        max_retries: int = 5,
    ) -> list[list[float]]:
        """Call Azure OpenAI embedding API with exponential backoff."""
        texts = [_truncate_to_token_limit(t) for t in texts]
        for attempt in range(max_retries):
            try:
                response = await self.client.embeddings.create(
                    input=texts,
                    model=self._deployment,
                    dimensions=self._dimensions,
                )
                # Sort by index to ensure order matches input
                sorted_data = sorted(response.data, key=lambda x: x.index)
                return [item.embedding for item in sorted_data]

            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate" in error_str.lower()

                if is_rate_limit and attempt < max_retries - 1:
                    wait_time = 2**attempt
                    logger.warn(
                        "embedder.rate_limited",
                        attempt=attempt + 1,
                        wait_seconds=wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                logger.error(
                    "embedder.api_error",
                    error=error_str,
                    attempt=attempt + 1,
                )
                raise
