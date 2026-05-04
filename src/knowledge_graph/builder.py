"""Neo4j knowledge graph builder — indexes documents and builds relationships."""

from __future__ import annotations

import structlog
from neo4j import AsyncDriver, AsyncGraphDatabase

from config.settings import get_settings
from src.knowledge_graph import queries, schema

logger = structlog.get_logger()

_driver: AsyncDriver | None = None


async def get_neo4j_driver() -> AsyncDriver:
    """Get or create the async Neo4j driver singleton."""
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
            connection_acquisition_timeout=settings.NEO4J_CONNECTION_ACQUISITION_TIMEOUT,
            connection_timeout=5,
        )
    return _driver


async def close_neo4j_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def init_neo4j_schema() -> None:
    """Create constraints and indexes in Neo4j."""
    driver = await get_neo4j_driver()
    async with driver.session() as session:
        for constraint in schema.CONSTRAINTS:
            try:
                await session.run(constraint)
            except Exception as e:
                logger.debug("neo4j.constraint_exists", query=constraint, error=str(e))

        for index in schema.INDEXES:
            try:
                await session.run(index)
            except Exception as e:
                logger.debug("neo4j.index_exists", query=index, error=str(e))

    logger.info("neo4j.schema_initialized")


class GraphBuilder:
    """Builds and maintains the Neo4j knowledge graph."""

    def __init__(self, driver: AsyncDriver | None = None):
        self._driver = driver

    async def _get_driver(self) -> AsyncDriver:
        if self._driver is not None:
            return self._driver
        return await get_neo4j_driver()

    async def index_document(
        self,
        doc_id: str,
        title: str,
        department: str | None,
        content_type: str | None,
        access_level: str | None,
        summary: str | None,
        sharepoint_url: str,
        file_type: str,
        created_at: str,
        chunks: list[dict],
        entities: list[dict],
        topics: list[dict],
    ) -> None:
        """
        Index a document and its chunks, entities, and topics in Neo4j.

        Args:
            doc_id: UUID string.
            chunks: List of dicts with chunk_id, chunk_index, summary.
            entities: List of dicts with name, type, aliases, count, sections, chunk_ids.
            topics: List of dicts with name, department, relevance.
        """
        driver = await self._get_driver()
        async with driver.session() as session:
            # 1. Create Document node
            await session.run(
                queries.CREATE_DOCUMENT,
                doc_id=doc_id,
                title=title,
                department=department,
                content_type=content_type,
                access_level=access_level,
                summary=summary,
                sharepoint_url=sharepoint_url,
                file_type=file_type,
                created_at=created_at,
            )

            # 2. Create Chunk nodes with CONTAINS + NEXT_CHUNK chains
            for chunk in chunks:
                await session.run(
                    queries.CREATE_CHUNK,
                    chunk_id=chunk["chunk_id"],
                    doc_id=doc_id,
                    chunk_index=chunk["chunk_index"],
                    summary=chunk.get("summary", ""),
                )

            for i in range(len(chunks) - 1):
                await session.run(
                    queries.LINK_NEXT_CHUNKS,
                    doc_id=doc_id,
                    index_a=chunks[i]["chunk_index"],
                    index_b=chunks[i + 1]["chunk_index"],
                )

            # 3. Link to department
            if department:
                await session.run(
                    queries.LINK_DEPARTMENT,
                    doc_id=doc_id,
                    department=department,
                )

            # 4. Create Entity nodes + relationships
            for entity in entities:
                await session.run(
                    queries.CREATE_ENTITY,
                    name=entity["name"],
                    type=entity["type"],
                    aliases=entity.get("aliases", []),
                )
                await session.run(
                    queries.LINK_ENTITY_TO_DOCUMENT,
                    name=entity["name"],
                    type=entity["type"],
                    doc_id=doc_id,
                    count=entity.get("count", 1),
                    sections=entity.get("sections", []),
                )
                for chunk_id in entity.get("chunk_ids", []):
                    await session.run(
                        queries.LINK_ENTITY_TO_CHUNK,
                        name=entity["name"],
                        type=entity["type"],
                        chunk_id=chunk_id,
                    )

            # 5. Create Topic nodes + relationships
            for topic in topics:
                await session.run(
                    queries.CREATE_TOPIC,
                    name=topic["name"],
                    department=topic.get("department"),
                )
                await session.run(
                    queries.LINK_TOPIC_TO_DOCUMENT,
                    name=topic["name"],
                    doc_id=doc_id,
                    relevance=topic.get("relevance", 0.5),
                )

            # 6. Materialize shared entity/topic relationships
            await session.run(
                queries.MATERIALIZE_SHARED_ENTITY,
                doc_id=doc_id,
            )
            await session.run(
                queries.MATERIALIZE_SHARED_TOPIC,
                doc_id=doc_id,
            )

        logger.info(
            "graph_builder.indexed",
            doc_id=doc_id,
            chunks=len(chunks),
            entities=len(entities),
            topics=len(topics),
        )

    async def delete_document(self, doc_id: str) -> None:
        """Remove a document and all its chunks/relationships from Neo4j."""
        driver = await self._get_driver()
        async with driver.session() as session:
            await session.run(queries.DELETE_DOCUMENT_GRAPH, doc_id=doc_id)
        logger.info("graph_builder.deleted", doc_id=doc_id)

    async def expand_from_documents(
        self,
        doc_ids: list[str],
        access_levels: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Graph traversal: expand from vector search results to find related documents.

        Args:
            doc_ids: Document IDs from vector search results.
            access_levels: User's allowed access levels for RBAC filtering.
            limit: Max number of related documents to return.

        Returns:
            List of related document dicts with title, summary, sharepoint_url, etc.
        """
        if not doc_ids:
            return []

        driver = await self._get_driver()
        async with driver.session() as session:
            result = await session.run(
                queries.GRAPH_EXPANSION,
                doc_ids=doc_ids,
                access_levels=access_levels or ["public", "internal"],
                limit=limit,
            )
            records = await result.data()

        logger.info(
            "graph_builder.expanded",
            source_docs=len(doc_ids),
            related_docs=len(records),
        )
        return records
