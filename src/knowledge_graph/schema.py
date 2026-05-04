"""Neo4j schema definitions — node types, relationship types, constraints, indexes."""

# Cypher statements to set up the Neo4j knowledge graph schema.

CONSTRAINTS = [
    "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT entity_name_type IF NOT EXISTS FOR (e:Entity) REQUIRE (e.name, e.type) IS UNIQUE",
    "CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT dept_name IF NOT EXISTS FOR (d:Department) REQUIRE d.name IS UNIQUE",
]

INDEXES = [
    "CREATE INDEX doc_department IF NOT EXISTS FOR (d:Document) ON (d.department)",
    "CREATE INDEX doc_content_type IF NOT EXISTS FOR (d:Document) ON (d.content_type)",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX chunk_doc IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id)",
]

# Node property definitions (for documentation / validation)
NODE_TYPES = {
    "Document": [
        "doc_id",           # UUID string
        "title",
        "department",
        "content_type",
        "access_level",
        "summary",
        "sharepoint_url",
        "file_type",
        "created_at",
    ],
    "Chunk": [
        "chunk_id",         # UUID string
        "doc_id",
        "chunk_index",
        "summary",
    ],
    "Entity": [
        "name",
        "type",             # PERSON, ORG, PRODUCT, POLICY, METRIC, PROJECT
        "aliases",          # list of alternative names
    ],
    "Topic": [
        "name",
        "department",
    ],
    "Department": [
        "name",
        "description",
    ],
}

RELATIONSHIP_TYPES = {
    "CONTAINS":          "(Document)-[:CONTAINS]->(Chunk)",
    "NEXT_CHUNK":        "(Chunk)-[:NEXT_CHUNK]->(Chunk)",
    "CITES":             "(Document)-[:CITES]->(Document)",
    "RELATED_TO":        "(Document)-[:RELATED_TO {strength, source}]->(Document)",
    "SUPERSEDES":        "(Document)-[:SUPERSEDES]->(Document)",
    "BELONGS_TO":        "(Document)-[:BELONGS_TO]->(Department)",
    "MENTIONED_IN":      "(Entity)-[:MENTIONED_IN {count, sections}]->(Document)",
    "MENTIONED_IN_CHUNK": "(Entity)-[:MENTIONED_IN_CHUNK]->(Chunk)",
    "COVERED_IN":        "(Topic)-[:COVERED_IN {relevance}]->(Document)",
    "SHARES_ENTITY":     "(Document)-[:SHARES_ENTITY {entity_name, count}]->(Document)",
    "SHARES_TOPIC":      "(Document)-[:SHARES_TOPIC {topic_name}]->(Document)",
}
