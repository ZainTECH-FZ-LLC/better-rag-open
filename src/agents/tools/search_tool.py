"""Search tool — retrieval-as-tool wrapper for department agents."""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import tool

logger = structlog.get_logger()


def create_search_tool(user_id: str, department: str | None = None):
    """
    Create a search tool bound to a specific user's RBAC context.

    The tool is injected into department agents so they can perform
    additional targeted searches during reasoning.
    """

    @tool
    async def search_documents(
        query: str,
        department_filter: str | None = None,
        content_type_filter: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search the enterprise document knowledge base.

        Use this tool when you need to look up specific information not covered
        by the initially retrieved context. Results are automatically filtered
        to documents the current user is authorized to access.

        Args:
            query: Natural language search query.
            department_filter: Optional department to restrict search (hr, finance, sales, marketing).
            content_type_filter: Optional content type (policy, report, presentation, memo).
            k: Number of results to return (1-10).

        Returns:
            List of relevant document chunks with content and source metadata.
        """
        from config.settings import get_settings
        from src.embedding.azure_openai import AzureOpenAIEmbedder
        from src.retrieval.metadata_filter import MetadataFilter
        from src.retrieval.vector_search import VectorSearchEngine
        from src.storage.db import get_db_session

        settings = get_settings()
        k = max(1, min(k, 10))

        try:
            embedder = AzureOpenAIEmbedder()
            query_embedding = await embedder.embed_query(query)

            filters = MetadataFilter(
                department=department_filter or department,
                content_type=content_type_filter,
            )

            async with get_db_session() as db:
                engine = VectorSearchEngine(db)
                results = await engine.cosine_search(
                    query_embedding=query_embedding,
                    user_id=user_id,
                    filters=filters,
                    k=k,
                )

            return [
                {
                    "content": r.content[:800],
                    "title": r.document_title,
                    "sharepoint_url": r.sharepoint_url,
                    "section": r.section_heading,
                    "department": r.department,
                    "score": round(r.score, 3),
                }
                for r in results
            ]

        except Exception as e:
            logger.error("search_tool.failed", query=query, error=str(e))
            return [{"error": f"Search failed: {str(e)}"}]

    return search_document


def create_search_tool(user_id: str, department: str | None = None):
    """Factory that returns the search tool with bound user context."""

    async def _search_impl(
        query: str,
        department_filter: str | None = None,
        content_type_filter: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        from config.settings import get_settings
        from src.embedding.azure_openai import AzureOpenAIEmbedder
        from src.retrieval.metadata_filter import MetadataFilter
        from src.retrieval.vector_search import VectorSearchEngine
        from src.storage.db import get_db_session

        k = max(1, min(k, 10))
        try:
            embedder = AzureOpenAIEmbedder()
            query_embedding = await embedder.embed_query(query)

            filters = MetadataFilter(
                department=department_filter or department,
                content_type=content_type_filter,
            )

            async with get_db_session() as db:
                engine = VectorSearchEngine(db)
                results = await engine.cosine_search(
                    query_embedding=query_embedding,
                    user_id=user_id,
                    filters=filters,
                    k=k,
                )

            return [
                {
                    "content": r.content[:800],
                    "title": r.document_title,
                    "sharepoint_url": r.sharepoint_url,
                    "section": r.section_heading,
                    "department": r.department,
                    "score": round(r.score, 3),
                }
                for r in results
            ]
        except Exception as e:
            logger.error("search_tool.failed", query=query, error=str(e))
            return [{"error": str(e)}]

    @tool
    async def search_documents(
        query: str,
        department_filter: str | None = None,
        content_type_filter: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search the enterprise document knowledge base.

        Use when you need additional information not in the retrieved context.
        Results are RBAC-filtered to documents the user is authorized to see.

        Args:
            query: Natural language search query.
            department_filter: Restrict to a department (hr, finance, sales, marketing).
            content_type_filter: Restrict to content type (policy, report, presentation).
            k: Number of results (1-10).

        Returns:
            List of document chunks with content, source, and relevance score.
        """
        return await _search_impl(query, department_filter, content_type_filter, k)

    return search_documents
