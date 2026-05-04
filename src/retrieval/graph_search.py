"""Graph search — Neo4j traversal for cross-document context expansion."""

from __future__ import annotations

import structlog

from src.knowledge_graph.builder import GraphBuilder, get_neo4j_driver
from src.knowledge_graph import queries
from src.storage.vector_store import ChunkResult

logger = structlog.get_logger()

# Edge weights for ranking related documents
_EDGE_WEIGHTS = {
    "SUPERSEDES": 0.95,
    "CITES": 0.90,
    "SHARES_ENTITY": 0.70,  # scaled by entity count below
    "RELATED_TO": 0.60,
    "SHARES_TOPIC": 0.50,
}


class GraphSearchEngine:
    """
    Expands retrieval results by traversing the Neo4j knowledge graph.

    Starting from documents found via vector search, it traverses 1-2 hops through:
    - CITES relationships (explicit document references)
    - SUPERSEDES relationships (policy versioning chains)
    - SHARES_ENTITY relationships (documents mentioning the same entities)
    - RELATED_TO relationships (manually tagged or metadata-derived)

    Results are RBAC-filtered at the Cypher level and ranked by edge weight.
    """

    def __init__(self, builder: GraphBuilder | None = None) -> None:
        self._builder = builder or GraphBuilder()

    async def expand(
        self,
        vector_results: list[ChunkResult],
        user_access_levels: list[str] | None = None,
        max_related: int = 10,
        max_hops: int = 2,
    ) -> list[dict]:
        """
        Expand from vector search results to find related documents via graph traversal.

        Args:
            vector_results: Documents found by vector similarity search.
            user_access_levels: User's allowed access levels (for RBAC Cypher filtering).
            max_related: Maximum number of related documents to return.
            max_hops: Maximum graph traversal depth (1 or 2).

        Returns:
            List of related document dicts with title, summary, sharepoint_url, score, etc.
        """
        if not vector_results:
            return []

        doc_ids = list({r.document_id for r in vector_results})
        access_levels = user_access_levels or ["public", "internal", "confidential"]

        try:
            driver = await self._builder._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    queries.GRAPH_EXPANSION,
                    doc_ids=doc_ids,
                    access_levels=access_levels,
                    limit=max_related,
                )
                records = await result.data()
        except Exception as e:
            logger.warn("graph_search.expansion_failed", error=str(e))
            return []

        # Score and deduplicate
        scored = _score_and_deduplicate(records, doc_ids)

        logger.info(
            "graph_search.expand_complete",
            source_docs=len(doc_ids),
            related_docs=len(scored),
        )
        return scored[:max_related]

    async def find_related_by_entity(
        self,
        entity_name: str,
        entity_type: str,
        user_access_levels: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Find documents mentioning a specific entity.

        Useful for targeted entity-based queries like "show all docs mentioning Project Alpha".
        """
        access_levels = user_access_levels or ["public", "internal", "confidential"]

        cypher = """
        MATCH (e:Entity {name: $name, type: $type})-[:MENTIONED_IN]->(d:Document)
        WHERE d.access_level IN $access_levels
        RETURN d.doc_id AS doc_id,
               d.title AS title,
               d.summary AS summary,
               d.sharepoint_url AS sharepoint_url,
               d.department AS department
        LIMIT $limit
        """

        try:
            driver = await self._builder._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    cypher,
                    name=entity_name,
                    type=entity_type,
                    access_levels=access_levels,
                    limit=limit,
                )
                return await result.data()
        except Exception as e:
            logger.warn("graph_search.entity_search_failed", error=str(e))
            return []

    async def find_superseding_documents(
        self,
        doc_ids: list[str],
        user_access_levels: list[str] | None = None,
    ) -> list[dict]:
        """
        For each document, check if a newer version exists (SUPERSEDES relationship).

        Returns the latest version(s) if found.
        """
        access_levels = user_access_levels or ["public", "internal", "confidential"]

        cypher = """
        MATCH (newer:Document)-[:SUPERSEDES]->(d:Document)
        WHERE d.doc_id IN $doc_ids
        AND newer.access_level IN $access_levels
        RETURN newer.doc_id AS doc_id,
               newer.title AS title,
               newer.summary AS summary,
               newer.sharepoint_url AS sharepoint_url,
               d.doc_id AS supersedes_doc_id
        """

        try:
            driver = await self._builder._get_driver()
            async with driver.session() as session:
                result = await session.run(
                    cypher,
                    doc_ids=doc_ids,
                    access_levels=access_levels,
                )
                return await result.data()
        except Exception as e:
            logger.warn("graph_search.supersede_check_failed", error=str(e))
            return []

    async def get_document_context(self, doc_id: str) -> dict:
        """
        Retrieve a document's full graph context: entities, topics, citations.

        Used for enriching retrieval results with cross-document signals.
        """
        cypher = """
        MATCH (d:Document {doc_id: $doc_id})
        OPTIONAL MATCH (e:Entity)-[:MENTIONED_IN]->(d)
        OPTIONAL MATCH (t:Topic)-[:COVERED_IN]->(d)
        OPTIONAL MATCH (d)-[:CITES]->(cited:Document)
        RETURN d.title AS title,
               d.summary AS summary,
               collect(DISTINCT {name: e.name, type: e.type}) AS entities,
               collect(DISTINCT t.name) AS topics,
               collect(DISTINCT cited.title) AS citations
        """
        try:
            driver = await self._builder._get_driver()
            async with driver.session() as session:
                result = await session.run(cypher, doc_id=doc_id)
                record = await result.single()
                return dict(record) if record else {}
        except Exception as e:
            logger.warn("graph_search.context_failed", doc_id=doc_id, error=str(e))
            return {}


def _score_and_deduplicate(records: list[dict], source_doc_ids: list[str]) -> list[dict]:
    """
    Score related documents by edge type and deduplicate.

    Excludes documents already in the source set (they were found by vector search).
    Uses the pre-computed max_weight from the Cypher GRAPH_EXPANSION query,
    and falls back to Python scoring if not available.
    """
    seen: set[str] = set(source_doc_ids)
    scored: list[dict] = []

    for record in records:
        doc_id = record.get("doc_id") or record.get("related_doc_id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)

        # Use pre-computed max_weight from Cypher (already accounts for edge type)
        graph_score = record.get("max_weight")

        # relationship_types is a list from GRAPH_EXPANSION; pick the strongest
        rel_types = record.get("relationship_types") or []
        primary_rel = rel_types[0] if rel_types else "RELATED_TO"

        # Fallback scoring if max_weight not in result
        if graph_score is None:
            graph_score = _EDGE_WEIGHTS.get(primary_rel, 0.5)

        scored.append({
            "doc_id": doc_id,
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "sharepoint_url": record.get("sharepoint_url", ""),
            "department": record.get("department"),
            "relationship_types": rel_types,
            "graph_score": float(graph_score),
        })

    scored.sort(key=lambda x: x["graph_score"], reverse=True)
    return scored
