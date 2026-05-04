"""Cypher query templates for the knowledge graph."""

# ── Document & Chunk Ingestion ──

CREATE_DOCUMENT = """
MERGE (d:Document {doc_id: $doc_id})
SET d.title = $title,
    d.department = $department,
    d.content_type = $content_type,
    d.access_level = $access_level,
    d.summary = $summary,
    d.sharepoint_url = $sharepoint_url,
    d.file_type = $file_type,
    d.created_at = CASE WHEN $created_at IS NOT NULL AND $created_at <> '' THEN datetime($created_at) ELSE null END
"""

CREATE_CHUNK = """
MERGE (c:Chunk {chunk_id: $chunk_id})
SET c.doc_id = $doc_id,
    c.chunk_index = $chunk_index,
    c.summary = $summary
WITH c
MATCH (d:Document {doc_id: $doc_id})
MERGE (d)-[:CONTAINS]->(c)
"""

LINK_NEXT_CHUNKS = """
MATCH (c1:Chunk {doc_id: $doc_id, chunk_index: $index_a})
MATCH (c2:Chunk {doc_id: $doc_id, chunk_index: $index_b})
MERGE (c1)-[:NEXT_CHUNK]->(c2)
"""

LINK_DEPARTMENT = """
MERGE (dept:Department {name: $department})
WITH dept
MATCH (d:Document {doc_id: $doc_id})
MERGE (d)-[:BELONGS_TO]->(dept)
"""

# ── Entity & Topic ──

CREATE_ENTITY = """
MERGE (e:Entity {name: $name, type: $type})
ON CREATE SET e.aliases = $aliases
"""

LINK_ENTITY_TO_DOCUMENT = """
MATCH (e:Entity {name: $name, type: $type})
MATCH (d:Document {doc_id: $doc_id})
MERGE (e)-[r:MENTIONED_IN]->(d)
SET r.count = $count, r.sections = $sections
"""

LINK_ENTITY_TO_CHUNK = """
MATCH (e:Entity {name: $name, type: $type})
MATCH (c:Chunk {chunk_id: $chunk_id})
MERGE (e)-[:MENTIONED_IN_CHUNK]->(c)
"""

CREATE_TOPIC = """
MERGE (t:Topic {name: $name})
SET t.department = $department
"""

LINK_TOPIC_TO_DOCUMENT = """
MATCH (t:Topic {name: $name})
MATCH (d:Document {doc_id: $doc_id})
MERGE (t)-[r:COVERED_IN]->(d)
SET r.relevance = $relevance
"""

# ── Cross-Document Relationships ──

CREATE_CITES = """
MATCH (d1:Document {doc_id: $from_id})
MATCH (d2:Document {doc_id: $to_id})
MERGE (d1)-[:CITES]->(d2)
"""

CREATE_RELATED = """
MATCH (d1:Document {doc_id: $from_id})
MATCH (d2:Document {doc_id: $to_id})
MERGE (d1)-[r:RELATED_TO]->(d2)
SET r.strength = $strength, r.source = $source
"""

CREATE_SUPERSEDES = """
MATCH (d1:Document {doc_id: $new_id})
MATCH (d2:Document {doc_id: $old_id})
MERGE (d1)-[:SUPERSEDES]->(d2)
"""

MATERIALIZE_SHARED_ENTITY = """
MATCH (e:Entity)-[:MENTIONED_IN]->(d1:Document {doc_id: $doc_id})
MATCH (e)-[:MENTIONED_IN]->(d2:Document)
WHERE d1 <> d2
WITH d1, d2, collect(e.name) AS shared_entities, count(e) AS shared_count
WHERE shared_count >= 2
MERGE (d1)-[r:SHARES_ENTITY]->(d2)
SET r.entity_names = shared_entities, r.count = shared_count
"""

MATERIALIZE_SHARED_TOPIC = """
MATCH (t:Topic)-[:COVERED_IN]->(d1:Document {doc_id: $doc_id})
MATCH (t)-[:COVERED_IN]->(d2:Document)
WHERE d1 <> d2
WITH d1, d2, collect(t.name) AS shared_topics
MERGE (d1)-[r:SHARES_TOPIC]->(d2)
SET r.topic_names = shared_topics
"""

# ── Retrieval (Graph Traversal) ──

GRAPH_EXPANSION = """
MATCH (d:Document)
WHERE d.doc_id IN $doc_ids
CALL {
    WITH d
    MATCH (d)-[r:CITES|RELATED_TO|SHARES_ENTITY|SUPERSEDES]-(related:Document)
    WHERE related.access_level IN $access_levels OR related.access_level IS NULL
    RETURN related, type(r) AS rel_type,
           CASE type(r)
               WHEN 'SUPERSEDES' THEN 0.95
               WHEN 'CITES' THEN 0.9
               WHEN 'SHARES_ENTITY' THEN 0.5 + (COALESCE(r.count, 0) * 0.05)
               WHEN 'RELATED_TO' THEN COALESCE(r.strength, 0.5)
               ELSE 0.3
           END AS edge_weight
    LIMIT 20
}
RETURN DISTINCT
    related.doc_id AS doc_id,
    related.title AS title,
    related.summary AS summary,
    related.sharepoint_url AS sharepoint_url,
    related.department AS department,
    collect(DISTINCT rel_type) AS relationship_types,
    max(edge_weight) AS max_weight
ORDER BY max_weight DESC
LIMIT $limit
"""

# ── Deletion ──

DELETE_DOCUMENT_GRAPH = """
MATCH (d:Document {doc_id: $doc_id})
OPTIONAL MATCH (d)-[:CONTAINS]->(c:Chunk)
DETACH DELETE c
WITH d
DETACH DELETE d
"""
