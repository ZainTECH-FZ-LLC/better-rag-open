# Agentic RAG System - Implementation Plan

## Context

Build a greenfield enterprise agentic RAG system that ingests documents from SharePoint/OneDrive with full RBAC enforcement, processes them through an OCR-capable pipeline with adaptive semantic chunking, stores them in pgvector with a Neo4j knowledge graph for cross-document relationships, and serves queries through a LangGraph-based multi-agent framework with department-specific sub-agents that can generate PPTX/DOCX/XLSX outputs from department formatting templates.

---

## Architecture Overview

```
SharePoint/OneDrive (via Microsoft Graph API)
        |
    [1. Source Connector] ── captures RBAC from Entra ID
        |
    [2. Azure Blob Storage] ── staging layer
        |
    [3. Document Processing]
        |   ├── File-type parsers (pymupdf, python-pptx, openpyxl, python-docx)
        |   ├── Vision extraction (GPT-4.1-mini) ── PPTX slides & PDF pages → PNG → vision model
        |   ├── Azure Document Intelligence (OCR) ── fallback for scanned PDFs, XLSX augmentation
        |   ├── LLM metadata extraction (GPT-4.1-mini)
        |   └── LLM summarization (GPT-4.1-mini)
        |   [All 3 LLM tasks run concurrently via asyncio.gather]
        |
    [4. Adaptive Semantic Chunker] ── file-type-aware strategies
        |
    [5. Embedding] ── text-embedding-3-large (1536 dims, configurable)
        |              [Concurrent batch embedding — up to 4 batches in parallel]
        |
    ┌───┴───┐
    |       |
[pgvector] [Neo4j]
 (HNSW)    (knowledge graph)
    |       |
    └───┬───┘
        |
    [6. Retrieval Pipeline] ── HyDE → metadata filter → vector search → graph expansion → rerank
        |
    [7. LangGraph Orchestrator]
        |
    ┌───┼───┬───┬───┐
   HR  Fin Sales Mkt General  ── department sub-agents with custom prompts
    |   |    |    |    |
    └───┼────┴────┴────┘
        |
    [8. Document Generation] ── PPTX/DOCX/XLSX from department templates
```

---

## Project Structure

```
better-rag/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── alembic/                              # DB migrations
│   ├── alembic.ini
│   └── versions/
├── config/
│   ├── __init__.py
│   └── settings.py                       # Pydantic Settings (all config via env vars)
├── src/
│   ├── __init__.py
│   ├── cli.py                            # Typer CLI for running pipeline stages
│   ├── main.py                           # FastAPI entrypoint
│   │
│   ├── models/
│   │   ├── document.py                   # Document, Chunk, DocumentMetadata (SQLAlchemy)
│   │   ├── permission.py                 # AccessControlEntry, PermissionGrant
│   │   ├── state.py                      # LangGraph AgentState TypedDict
│   │   └── enums.py                      # FileType, ProcessingStatus, QueryType
│   │
│   ├── connectors/
│   │   ├── graph_client.py               # Microsoft Graph authenticated client (MSAL)
│   │   ├── sharepoint.py                 # SharePointConnector - fetch docs + metadata
│   │   ├── permissions.py                # PermissionResolver - Entra ID RBAC capture
│   │   └── delta_sync.py                 # DeltaSyncManager - incremental change tracking
│   │
│   ├── storage/
│   │   ├── blob_store.py                 # Azure Blob Storage upload/download
│   │   ├── vector_store.py               # PgVectorStore - pgvector HNSW search + RBAC filtering
│   │   └── db.py                         # Async SQLAlchemy session factory
│   │
│   ├── processing/
│   │   ├── pipeline.py                   # DocumentProcessingPipeline orchestrator
│   │   ├── ocr/
│   │   │   ├── azure_di.py              # Azure Document Intelligence (fallback OCR)
│   │   │   ├── vision_extractor.py      # GPT-4.1-mini vision — page/slide PNG → structured markdown
│   │   │   ├── slide_renderer.py        # PPTX → PDF (LibreOffice) → PNG (pymupdf)
│   │   │   ├── pdf_renderer.py          # PDF → PNG (pymupdf direct, no LibreOffice)
│   │   │   └── doctr_ocr.py             # doctr fallback (local/offline)
│   │   ├── parsers/
│   │   │   ├── base.py                  # Abstract DocumentParser
│   │   │   ├── pdf_parser.py            # PyMuPDF text extraction
│   │   │   ├── pptx_parser.py           # python-pptx (slides, charts, images, notes)
│   │   │   ├── xlsx_parser.py           # openpyxl + Azure DI augmentation
│   │   │   ├── docx_parser.py           # python-docx (headings, tables, properties)
│   │   │   └── parser_factory.py        # Route by file type
│   │   ├── metadata.py                  # MetadataExtractor (Graph API + file props + LLM via GPT-4.1-mini)
│   │   └── summarizer.py               # LLM document summarizer (GPT-4.1-mini)
│   │
│   ├── chunking/
│   │   ├── adaptive_chunker.py          # Main orchestrator - routes to strategies
│   │   ├── strategies/
│   │   │   ├── pdf_strategy.py          # Section/heading-based, tables as units
│   │   │   ├── pptx_strategy.py         # Slide-per-chunk, speaker notes included
│   │   │   ├── xlsx_strategy.py         # Sheet/data-region-based, headers repeated
│   │   │   └── docx_strategy.py         # Heading-hierarchy section tree
│   │   └── boundary_detector.py         # Semantic similarity for topic-shift detection
│   │
│   ├── embedding/
│   │   ├── base.py                      # Abstract EmbeddingProvider
│   │   └── azure_openai.py             # text-embedding-3-large with batch manager
│   │
│   ├── knowledge_graph/
│   │   ├── schema.py                    # Neo4j node/relationship type definitions
│   │   ├── builder.py                   # Automated relationship pipeline
│   │   ├── entity_extractor.py          # spaCy NER + LLM for domain entities
│   │   └── queries.py                   # Cypher query templates
│   │
│   ├── retrieval/
│   │   ├── hyde.py                      # HyDE query reformulation
│   │   ├── metadata_filter.py           # Pre-filter builder (RBAC + dept + date)
│   │   ├── vector_search.py             # Cosine + MMR search strategies
│   │   ├── graph_search.py              # Neo4j traversal for related context
│   │   └── reranker.py                  # BGE-reranker-v2-m3 (local cross-encoder)
│   │
│   ├── graph/
│   │   ├── orchestrator.py              # Top-level LangGraph StateGraph
│   │   ├── nodes/
│   │   │   ├── query_analyzer.py        # Classify query type, department, intent
│   │   │   ├── smart_router.py          # Deterministic retrieval strategy selection
│   │   │   ├── retriever.py             # Vector + graph retrieval node
│   │   │   ├── reranker.py              # Reranking node
│   │   │   ├── relevance_grader.py      # Self-reflection: grade retrieval quality
│   │   │   ├── response_synthesizer.py  # Final answer assembly
│   │   │   └── doc_generator.py         # Document generation node
│   │   ├── edges.py                     # Conditional edge routing functions
│   │   └── subgraphs/
│   │       ├── department_factory.py    # Factory: creates dept-specific subgraphs
│   │       ├── hr_agent.py
│   │       ├── finance_agent.py
│   │       ├── sales_agent.py
│   │       ├── marketing_agent.py
│   │       └── general_agent.py
│   │
│   ├── agents/
│   │   ├── prompts/
│   │   │   ├── supervisor.py            # Query analysis prompt
│   │   │   ├── hr.py                    # Policy-focused, compliance-aware
│   │   │   ├── finance.py               # Data-accuracy, tabular output
│   │   │   ├── sales.py                 # Action-oriented, visual, chart-heavy
│   │   │   ├── marketing.py             # Visual-forward, presentation-ready
│   │   │   └── general.py               # Balanced default
│   │   └── tools/
│   │       ├── search_tool.py           # Retrieval-as-tool wrapper
│   │       ├── doc_gen_tool.py          # Document generation tool
│   │       └── chart_tool.py            # Chart generation (matplotlib/plotly)
│   │
│   ├── skills/                          # Agent Skills (SKILL.md format per agentskills.io/spec)
│   │   ├── _loader.py                   # SkillLoader: progressive disclosure engine
│   │   ├── pptx/
│   │   │   ├── SKILL.md                 # Full PPTX skill (design, typography, QA loops)
│   │   │   ├── references/
│   │   │   │   ├── editing.md           # Template editing workflow
│   │   │   │   └── pptxgenjs.md         # From-scratch creation guide
│   │   │   ├── scripts/
│   │   │   │   ├── thumbnail.py         # Slide → image for visual QA
│   │   │   │   └── office/
│   │   │   │       ├── soffice.py       # LibreOffice wrapper
│   │   │   │       ├── unpack.py        # PPTX → XML
│   │   │   │       └── pack.py          # XML → PPTX
│   │   │   └── assets/
│   │   │       └── color_palettes.json
│   │   ├── docx/
│   │   │   ├── SKILL.md                 # Full DOCX skill (creation, editing, XML ref, QA)
│   │   │   ├── references/
│   │   │   │   └── xml_reference.md
│   │   │   └── scripts/
│   │   │       ├── accept_changes.py
│   │   │       ├── comment.py
│   │   │       └── office/
│   │   │           ├── soffice.py
│   │   │           ├── unpack.py
│   │   │           ├── pack.py
│   │   │           └── validate.py
│   │   ├── xlsx/
│   │   │   ├── SKILL.md                 # Full XLSX skill (formulas, formatting, recalc, QA)
│   │   │   ├── references/
│   │   │   │   └── financial_models.md
│   │   │   └── scripts/
│   │   │       └── recalc.py            # Formula recalc via LibreOffice
│   │   └── chart/
│   │       ├── SKILL.md                 # Chart generation skill
│   │       └── references/
│   │           └── chart_types.md
│   │
│   ├── document_generation/
│   │   ├── base.py                      # Abstract BaseDocumentGenerator
│   │   ├── pptx_generator.py            # pptxgenjs (scratch) + python-pptx (templates)
│   │   ├── docx_generator.py            # docx-js (scratch) + XML unpack/edit (templates)
│   │   ├── xlsx_generator.py            # openpyxl + recalc.py
│   │   ├── chart_builder.py             # matplotlib/plotly chart image builder
│   │   └── visual_qa.py                 # QA loop: render → inspect → fix → re-verify
│   │
│   └── templates/                       # Department formatting template repository
│       ├── shared/
│       │   ├── generic_report.docx
│       │   ├── generic_presentation.pptx
│       │   └── generic_spreadsheet.xlsx
│       ├── hr/
│       │   ├── policy_report.docx
│       │   ├── onboarding_deck.pptx
│       │   ├── headcount_tracker.xlsx
│       │   └── _style.json              # HR brand: colors, fonts, sizes
│       ├── finance/
│       │   ├── financial_report.docx
│       │   ├── quarterly_review.pptx
│       │   ├── budget_template.xlsx
│       │   └── _style.json
│       ├── sales/
│       │   ├── proposal.docx
│       │   ├── sales_deck.pptx
│       │   ├── pipeline_tracker.xlsx
│       │   └── _style.json
│       └── marketing/
│           ├── campaign_brief.docx
│           ├── brand_deck.pptx
│           ├── campaign_metrics.xlsx
│           └── _style.json
│
├── scripts/
│   ├── local_ingest.py                   # Local folder ingestion (no SharePoint needed)
│   │                                     # 4-phase DB session pattern:
│   │                                     #   Phase 1: Quick DB check (create/find doc)
│   │                                     #   Phase 2: Heavy processing outside DB session
│   │                                     #   Phase 3: Fast DB write (fresh session)
│   │                                     #   Phase 4: Neo4j indexing (best-effort)
│   └── test_query.py                     # Test retrieval pipeline
│
└── tests/
    ├── conftest.py
    ├── fixtures/                         # Sample PDF, PPTX, XLSX, DOCX files
    ├── unit/
    └── integration/
```

---

## Component Implementation Details

### 1. SharePoint/OneDrive Connector with RBAC

**Files:** `src/connectors/graph_client.py`, `sharepoint.py`, `permissions.py`, `delta_sync.py`

**Auth:** MSAL (`msal`) with client credentials flow for daemon/service pipeline. App registration needs: `Sites.Read.All`, `Files.Read.All`, `GroupMember.Read.All`, `User.Read.All`, `Directory.Read.All`.

**RBAC Capture (PermissionResolver):**
- For each document, call `GET /drives/{drive-id}/items/{item-id}/permissions` via Graph API
- Resolve group → user memberships transitively via `GET /groups/{id}/transitiveMembers`
- Store **both** group IDs (for efficient updates) and expanded user IDs (for fast query-time filtering)
- Background job re-expands groups every 4 hours to catch membership changes

**SharePoint Links:** Capture `driveItem.webUrl` from Graph API for every document — this is the direct browser-accessible SharePoint URL used for citations. Store on the document record and propagate to every chunk.

**Incremental Sync (DeltaSyncManager):**
- Uses Microsoft Graph delta queries: `GET /drives/{drive-id}/root/delta`
- First run returns all items + a `deltaLink` token; subsequent calls return only changes
- Delta tokens stored in `sync_cursors` table per (site_id, drive_id)
- Tokens valid ~30 days; proactively re-sync at 25 days before expiry
- Classifies changes into created/modified/deleted and routes accordingly

**Libraries:** `msgraph-sdk`, `msal`, `httpx`

---

### 2. Document Processing Pipeline with Vision + OCR

**Files:** `src/processing/pipeline.py`, `ocr/vision_extractor.py`, `ocr/slide_renderer.py`, `ocr/pdf_renderer.py`, `ocr/azure_di.py`, `parsers/*.py`, `metadata.py`, `summarizer.py`

**Primary: GPT-4.1-mini Vision Model** (for PPTX and PDF)
- Every PPTX slide and PDF page is rendered as a high-res PNG (200 DPI) and sent to a vision-capable LLM
- The vision model extracts charts, graphs, diagrams, tables, and any visual content that text parsers miss
- Parser-extracted text is provided as context to avoid duplication — the prompt instructs the model to only add what's missing
- Concurrency: up to 10 pages/slides processed in parallel via `asyncio.Semaphore`
- Prompts are generic — same extraction logic handles both PDF pages and PPTX slides

**PPTX Vision Pipeline:**
1. `slide_renderer.py`: PPTX → PDF via LibreOffice headless (WSL on Windows, native on Linux) → PNG per page via pymupdf
2. `vision_extractor.py`: Each slide PNG + parser text context → GPT-4.1-mini → structured markdown with chart data tables
3. Vision output merged into slide content → rebuilt as enriched full text

**PDF Vision Pipeline:**
1. `pdf_renderer.py`: PDF → PNG per page via pymupdf directly (no LibreOffice needed)
2. `vision_extractor.py`: Each page PNG + PyMuPDF text context → GPT-4.1-mini → structured markdown
3. Parser text + vision enrichment merged per page
4. **Fallback:** If vision fails on a scanned PDF, falls back to Azure DI OCR

**Azure Document Intelligence** (fallback / augmentation):
- Scanned PDFs without text layer: Azure DI `prebuilt-layout` model for OCR when vision endpoint is not configured
- XLSX: Azure DI augments table/chart extraction
- Feature: `ocrHighResolution` for dense PDFs (not valid for Office formats)
- Files > 6MB: auto-uploaded to Azure Blob with SAS URL for Azure DI processing

**File-Type Processing Summary:**

| File Type | Text Extraction | Visual Content (Charts/Graphs) | Fallback |
|-----------|----------------|-------------------------------|----------|
| **PDF** | PyMuPDF | Every page → PNG → GPT-4.1-mini vision | Azure DI OCR (scanned PDFs) |
| **PPTX** | python-pptx | Every slide → LibreOffice PDF → PNG → GPT-4.1-mini vision | Parser text only |
| **XLSX** | openpyxl | Azure DI table augmentation + per-sheet LLM summaries | Parser text only |
| **DOCX** | python-docx | Parser text only (no vision yet) | Parser text only |

**Metadata Extraction** (3 sources, merged by priority, via GPT-4.1-mini):
1. **SharePoint/Graph API** (highest): author, created_at, modified_at, modified_by, sharepoint_url, site_name, library_name
2. **File properties**: page_count, word_count (from OOXML core properties / PDF info dict)
3. **LLM-derived** (fills gaps): department (if not inferrable from site path), content_type (policy/report/presentation/memo), topics, entities, language

**LLM Summarization** (GPT-4.1-mini for speed — avoids reasoning token overhead of larger models):
- Documents < 8K tokens: single-pass summarization
- Larger documents: hierarchical map-reduce (summarize sections → combine)
- XLSX: per-sheet summaries (2-3 sentences each) — raw tabular data embeds poorly without semantic context
- Summary stored as a separate "summary chunk" in vector DB for broad-query matching
- Summary also prepended as context prefix to each chunk's embedding input (improves retrieval)
- Target: 200-500 tokens per summary

**Pipeline Concurrency Optimization:**
- Metadata extraction, summarization, and entity extraction (spaCy NER) run **concurrently** via `asyncio.gather` — not sequentially
- This reduces wall time from ~20s (3 sequential LLM calls) to ~8s (limited by slowest call)

**Libraries:** `azure-ai-documentintelligence`, `pymupdf`, `python-pptx`, `openpyxl`, `python-docx`, `openai`, `structlog`

---

### 3. Azure Blob Storage Staging

**File:** `src/storage/blob_store.py`

Documents are uploaded to Azure Blob Storage before processing. Path convention: `{site_name}/{library_name}/{item_id}/{filename}`.

**Rationale:** Decouples SharePoint sync from processing cadence, provides retryable source (SharePoint has rate limits), enables reprocessing without re-downloading, cheaper than repeated Graph API calls.

**Libraries:** `azure-storage-blob`, `azure-identity`

---

### 4. Adaptive Semantic Chunking

**Files:** `src/chunking/adaptive_chunker.py`, `strategies/*.py`, `boundary_detector.py`

**Core Principles:**
- Never split mid-sentence or mid-table-row
- Never orphan a heading from its content
- Tables and charts are standalone chunks (kept whole)
- Target: 400-512 tokens (configurable), overlap: 50-75 tokens
- Every chunk gets a context prefix: document summary + section heading

**File-Type Strategies:**

| Strategy | Primary Unit | Split Logic |
|----------|-------------|-------------|
| PDF | Section (by Azure DI heading roles) | Split at paragraph boundaries within sections; tables/figures standalone |
| PPTX | Slide | Each slide = 1 chunk; speaker notes appended; charts = data description chunk |
| XLSX | Sheet / data region | Each contiguous data region = 1 chunk; column headers repeated if split; charts serialized |
| DOCX | Heading-hierarchy section | Leaf sections as chunks; merge small siblings; split large sections at paragraph boundaries |

**Semantic Boundary Detection** (optional, configurable):
- For segments exceeding target chunk size, embed sentences and compute cosine similarity between consecutive sentences
- "Semantic drops" (similarity below threshold) = natural topic transitions = preferred split points
- Uses a lightweight embedding model for boundary detection to save cost
- Can be disabled globally for speed

**Chunk Data Model:**
```
Chunk {
  chunk_id, document_id, content, content_with_context (prefix + content),
  chunk_type (text | table | image_description | summary),
  sequence_number, page_numbers[], section_heading,
  sharepoint_url (for citation), metadata, access_control
}
```

**Libraries:** `langchain-text-splitters`, `spacy`, `tiktoken`, `numpy`

---

### 5. Embedding & Vector Storage (pgvector)

**Files:** `src/embedding/azure_openai.py`, `src/storage/vector_store.py`

**Embedding Model:** `text-embedding-3-large` via Azure OpenAI
- Default dimensions: **1536** (configurable up to 3072)
- 1536 dims = ~98% quality of full 3072 on MTEB, half the storage/memory
- Matryoshka property: can reduce later without re-embedding
- Batching: 16 inputs per API call with rate limiting and exponential backoff
- **Concurrent batching:** Up to 4 embedding batches fire in parallel via `asyncio.gather` + `Semaphore(4)` — reduces embedding time for large documents (e.g., 70 chunks → 5 batches → 2 rounds instead of 5 sequential)

**What gets embedded:** `content_with_context` (document summary prefix + section heading + chunk content), not raw content alone — this adds document-level signal to each chunk's embedding.

**PostgreSQL + pgvector Schema:**

```sql
-- Core tables
documents (id, source_id, site_id, drive_id, blob_path, sharepoint_url,
           file_type, title, summary, metadata JSONB, content_hash,
           processing_status, created_at, updated_at, deleted_at)

document_chunks (id, document_id FK, content, content_with_context,
                 chunk_type, sequence_number, page_numbers INT[],
                 section_heading, embedding vector(1536), token_count,
                 -- Denormalized for filter performance:
                 department, access_level, content_type, created_at)

-- RBAC tables
document_permissions (id, document_id FK, principal_type, principal_id, role)
document_user_access (document_id FK, user_id) -- expanded from groups, composite PK

-- Sync state
sync_cursors (site_id, drive_id, delta_link, last_sync_at)

-- HNSW Index (high recall config)
CREATE INDEX idx_chunks_embedding_cosine ON document_chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 24, ef_construction = 200);

-- Metadata filter indexes
CREATE INDEX idx_chunks_dept_access ON document_chunks(department, access_level);
CREATE INDEX idx_chunks_created_at ON document_chunks(created_at);
CREATE INDEX idx_user_access_user ON document_user_access(user_id);

-- Query-time settings (per session)
SET hnsw.ef_search = 200;              -- High recall
SET hnsw.iterative_scan = relaxed_order; -- Combine HNSW with WHERE filters
```

**RBAC Enforcement:** Every vector search JOINs `document_user_access` to ensure the user only sees authorized results. This is the security enforcement point — it uses pre-expanded user IDs for fast query-time filtering.

**Libraries:** `asyncpg`, `sqlalchemy[asyncio]`, `pgvector`, `alembic`

---

### 6. Neo4j Knowledge Graph

**Files:** `src/knowledge_graph/schema.py`, `builder.py`, `entity_extractor.py`, `queries.py`

**Node Types:**
- `Document` — doc_id, title, department, content_type, access_level, summary
- `Chunk` — chunk_id, doc_id, chunk_index, summary
- `Entity` — name, type (PERSON/ORG/PRODUCT/POLICY/METRIC/PROJECT), aliases
- `Topic` — name, department
- `Department` — name, description

**Relationship Types:**
- `(Document)-[:CONTAINS]->(Chunk)` — structural
- `(Chunk)-[:NEXT_CHUNK]->(Chunk)` — ordering within document
- `(Document)-[:CITES]->(Document)` — extracted from references/hyperlinks
- `(Document)-[:RELATED_TO {strength, source}]->(Document)` — from metadata or computed
- `(Document)-[:SUPERSEDES]->(Document)` — policy versioning
- `(Document)-[:BELONGS_TO]->(Department)`
- `(Entity)-[:MENTIONED_IN]->(Document)` — with count and sections
- `(Entity)-[:MENTIONED_IN_CHUNK]->(Chunk)`
- `(Topic)-[:COVERED_IN]->(Document)` — with relevance score
- `(Document)-[:SHARES_ENTITY {entity_name, count}]->(Document)` — materialized for fast traversal
- `(Document)-[:SHARES_TOPIC {topic_name}]->(Document)` — materialized for fast traversal

**Automated Relationship Building Pipeline** (runs after each document ingestion):
1. Create `Document` and `Chunk` nodes with `NEXT_CHUNK` chain
2. Extract entities: **spaCy NER** for standard entities (PERSON, ORG, DATE, MONEY) + **cheap LLM call** for domain-specific entities (policy names, product names, project codes)
3. Create `Entity` nodes with `MENTIONED_IN` relationships
4. Classify topics via cheap LLM, create `Topic` nodes with `COVERED_IN` relationships
5. Process explicit `related_documents` metadata → `RELATED_TO` edges
6. Extract citations/references from document text → `CITES` edges
7. **Materialize** `SHARES_ENTITY` and `SHARES_TOPIC` edges (precomputed for fast graph traversal during retrieval). Threshold: only materialize when overlap count >= 2

**Graph Traversal During Retrieval:**
- Start from documents found via vector search
- Traverse 1-2 hops through `CITES`, `RELATED_TO`, `SHARES_ENTITY`, `SUPERSEDES` relationships
- RBAC-filtered at the Cypher level (`WHERE related.access_level IN $access_levels`)
- Returns related document summaries and top chunk summaries for context enrichment
- Edge weights used for ranking: SUPERSEDES (0.95) > CITES (0.9) > SHARES_ENTITY (scaled by count) > RELATED_TO (stored strength)

**Libraries:** `neo4j` (async driver), `spacy`

---

### 7. Retrieval Pipeline

**Files:** `src/retrieval/hyde.py`, `metadata_filter.py`, `vector_search.py`, `graph_search.py`, `reranker.py`

**Full retrieval flow (10 steps):**

```
User Query
  → [1] Query Analysis (cheap LLM: classify type, department, temporal hints, doc gen intent)
  → [2] Smart Router (deterministic, zero LLM cost)
        factual → cosine | analytical/generative → HyDE+cosine | procedural → MMR
  → [3] HyDE (if needed: cheap LLM generates hypothetical answer paragraph, embed that)
  → [4] Metadata Filter Builder (RBAC user access + department + date range + content type → SQL WHERE)
  → [5] Embed query (or HyDE output) via text-embedding-3-large
  → [6] pgvector HNSW search (k=20, ef_search=200, iterative_scan=relaxed_order, with metadata WHERE)
  → [7] Neo4j graph traversal (expand to related docs via entity/citation/topic links)
  → [8] Result Merger (deduplicate vector + graph results)
  → [9] BGE Reranker (cross-encoder, local inference, ~100-200ms for 20 candidates)
  → [10] Relevance Grader (cheap LLM grades top 3; if <2 relevant, retry HyDE once)
  → Top 8 results → department agent
```

**MMR Implementation** (application-level, since pgvector doesn't support MMR natively):
- Fetch larger candidate set (fetch_k=60) via cosine search
- Apply MMR selection in Python: balance relevance vs diversity with lambda_mult=0.7
- Return top-k diverse results

**Reranker: BGE-reranker-v2-m3** (local cross-encoder, zero API cost)
- Open-source, runs locally via `sentence-transformers`
- Processes 20 candidate pairs in ~100-200ms on CPU
- Multilingual support
- Upgrade path: Cohere Rerank API if higher throughput needed later

**Libraries:** `sentence-transformers` (BGE reranker), `numpy` (MMR computation)

---

### 8. LangGraph Agentic Framework

**Files:** `src/graph/orchestrator.py`, `nodes/*.py`, `edges.py`, `subgraphs/*.py`

**AgentState** (TypedDict flowing through entire graph):
```python
AgentState {
  messages, user_context (UserContext with user_id/department/roles/access_level),
  original_query, reformulated_query, query_type, target_department,
  requires_document_generation, metadata_filters, retrieval_strategy,
  raw_results, reranked_results, graph_context,
  answer, generated_file_path,
  current_agent, iteration_count, should_retry_retrieval
}
```

**Graph Flow:**
```
START → query_analyzer → smart_router
  → [hyde_reformulator if needed] → metadata_filter_builder
  → vector_retriever → graph_retriever → result_merger
  → reranker → relevance_grader
  → [retry HyDE if insufficient, max 1 retry]
  → route_to_department (hr|finance|sales|marketing|general)
  → [document_generator if needed]
  → response_synthesizer → END
```

**Department Sub-Agents** (created via factory pattern):
Each is a LangGraph subgraph with: reasoning node (LLM with tools) → tool execution → loop back.

| Department | Temperature | Context Chunks | Key Tools | Output Style |
|-----------|------------|---------------|-----------|-------------|
| HR | 0.1 | 6 | search, doc_gen | Structured lists, policy citations, compliance refs |
| Finance | 0.0 | 8 | search, doc_gen, chart | Tables, precise numbers, period labels, GAAP refs |
| Sales | 0.3 | 6 | search, doc_gen, chart | Visual, action-oriented, executive summaries, chart suggestions |
| Marketing | 0.4 | 6 | search, doc_gen, chart | Presentation-ready, visual-forward, brand-consistent |
| General | 0.2 | 5 | search, doc_gen | Balanced, clear language |

**LLM Cost Optimization** (by design):
- Query analysis: cheap model (gpt-4o-mini / claude-haiku)
- Smart router: deterministic Python logic, zero LLM cost
- HyDE: cheap model (output is only used for embedding)
- Relevance grading: cheap model, binary yes/no, only top 3 results
- Entity extraction: spaCy for 80% (zero cost), cheap LLM for domain-specific
- Reranking: local BGE model, zero API cost
- **Only the department agent reasoning uses the expensive model** (gpt-4o / claude-sonnet), runs 1-2 times per request

**LLM Provider Abstraction:** Both Azure OpenAI and Anthropic Claude are supported, configurable via env vars. An abstract `LLMProvider` wrapper in `config/settings.py` selects the appropriate `langchain-openai` or `langchain-anthropic` client based on `LLM_PROVIDER` config. Default: Azure OpenAI (GPT-4o / GPT-4o-mini) for Azure ecosystem alignment.

**Libraries:** `langgraph`, `langchain-core`, `langchain-openai`, `langchain-anthropic`

---

### 9. Document Generation — Agent Skills Architecture

The document generation system combines **Anthropic's Agent Skills spec** (SKILL.md format from [github.com/anthropics/skills](https://github.com/anthropics/skills)) with **LangGraph's tool registration pattern** to give every department agent production-grade document creation capabilities.

#### 9.1 Design: Two-Layer Architecture

```
Layer 1: Agent Skills (SKILL.md files)
  └── Detailed instructions, best practices, QA checklists, code patterns
  └── Loaded via progressive disclosure (metadata at startup, full content on-demand)
  └── Based on Anthropic's Agent Skills spec (agentskills.io/specification)

Layer 2: LangGraph Tools
  └── Concrete tool functions that agents invoke
  └── Tools use the skill instructions as context for LLM-driven generation
  └── Department templates resolved at tool execution time
```

**Why both layers?** The Agent Skills provide the LLM with expert-level knowledge about *how* to create high-quality documents (design principles, typography, formula rules, QA processes). The LangGraph tools provide the *execution mechanism* — the actual code that generates files. The LLM reads the skill, reasons about the content to produce, and calls the tool to render it.

#### 9.2 Skills Directory Structure

```
src/skills/
├── pptx/
│   ├── SKILL.md                      # Full PPTX creation skill (adapted from anthropics/skills)
│   ├── references/
│   │   ├── editing.md                # Template editing workflow
│   │   └── pptxgenjs.md             # Creating from scratch with pptxgenjs
│   ├── scripts/
│   │   ├── thumbnail.py             # Slide → image for visual QA
│   │   └── office/
│   │       ├── soffice.py           # LibreOffice wrapper
│   │       ├── unpack.py            # PPTX → XML extraction
│   │       └── pack.py              # XML → PPTX repacking
│   └── assets/
│       └── color_palettes.json      # Pre-defined department color palettes
│
├── docx/
│   ├── SKILL.md                      # Full DOCX creation skill (adapted from anthropics/skills)
│   ├── references/
│   │   └── xml_reference.md         # OOXML editing reference
│   ├── scripts/
│   │   ├── accept_changes.py        # Tracked changes management
│   │   ├── comment.py               # Comment insertion
│   │   ├── office/
│   │   │   ├── soffice.py
│   │   │   ├── unpack.py
│   │   │   ├── pack.py
│   │   │   └── validate.py          # DOCX validation
│   │   └── recalc.py
│   └── assets/
│       └── style_presets.json        # Department heading/font presets
│
├── xlsx/
│   ├── SKILL.md                      # Full XLSX creation skill (adapted from anthropics/skills)
│   ├── references/
│   │   └── financial_models.md       # Financial model best practices
│   ├── scripts/
│   │   └── recalc.py                # Formula recalculation via LibreOffice
│   └── assets/
│       └── number_formats.json       # Standard number format codes
│
├── chart/
│   ├── SKILL.md                      # Chart generation skill
│   └── references/
│       └── chart_types.md            # When to use bar vs line vs pie etc.
│
└── _loader.py                        # SkillLoader: progressive disclosure engine
```

#### 9.3 SKILL.md Format (Following Agent Skills Spec)

Each skill follows the [Agent Skills specification](https://agentskills.io/specification):

```yaml
---
name: pptx
description: "Use this skill any time a .pptx file is involved — creating slide decks,
  pitch decks, presentations; editing or updating existing presentations; working with
  templates, layouts, speaker notes. Trigger when user mentions 'deck', 'slides',
  'presentation', or references a .pptx filename."
license: Apache-2.0
compatibility: Requires node.js (pptxgenjs), LibreOffice (soffice), Poppler (pdftoppm)
metadata:
  author: better-rag
  version: "1.0"
  allowed-tools: Bash Read Write Edit
---

# PPTX Skill

## Quick Reference
| Task | Guide |
|------|-------|
| Read/analyze content | `python -m markitdown presentation.pptx` |
| Edit or create from template | Read [editing.md](references/editing.md) |
| Create from scratch | Read [pptxgenjs.md](references/pptxgenjs.md) |

## Design Ideas
[... color palettes, typography, layout options, spacing rules ...]

## QA (Required)
[... visual QA with subagents, verification loop, converting to images ...]
```

**Key adaptations from the Anthropic skills:**
- PPTX skill uses `pptxgenjs` (Node.js) for from-scratch creation — produces higher quality output than python-pptx for complex layouts
- DOCX skill uses `docx` npm package (docx-js) for creation, XML unpacking for editing existing files
- XLSX skill uses `openpyxl` for creation + `scripts/recalc.py` (LibreOffice) for formula recalculation
- All skills include **QA loops** — generate → convert to images → visual inspect → fix → re-verify

#### 9.4 Progressive Disclosure Loading

```python
# src/skills/_loader.py
import yaml
from pathlib import Path
from dataclasses import dataclass

@dataclass
class SkillMetadata:
    """~100 tokens. Loaded for ALL skills at agent startup."""
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict | None = None

@dataclass
class SkillFull:
    """< 5000 tokens recommended. Loaded when skill is activated."""
    meta: SkillMetadata
    instructions: str            # Full SKILL.md body

class SkillLoader:
    """Loads skills using progressive disclosure per Agent Skills spec."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._metadata_cache: dict[str, SkillMetadata] = {}
        self._load_all_metadata()

    def _load_all_metadata(self):
        """Stage 1: Load only YAML frontmatter for all skills (~100 tokens each)."""
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                frontmatter = self._parse_frontmatter(skill_md)
                self._metadata_cache[frontmatter["name"]] = SkillMetadata(**frontmatter)

    def get_all_metadata(self) -> list[SkillMetadata]:
        """Return metadata for all skills (for agent system prompt routing)."""
        return list(self._metadata_cache.values())

    def activate_skill(self, skill_name: str) -> SkillFull:
        """Stage 2: Load full SKILL.md body when skill is needed."""
        skill_md = self.skills_dir / skill_name / "SKILL.md"
        content = skill_md.read_text()
        _, body = content.split("---", 2)[1:]  # Split frontmatter from body
        body = body.strip()
        return SkillFull(
            meta=self._metadata_cache[skill_name],
            instructions=body
        )

    def read_reference(self, skill_name: str, ref_path: str) -> str:
        """Stage 3: Load reference/script files on demand."""
        file_path = self.skills_dir / skill_name / ref_path
        return file_path.read_text()
```

#### 9.5 Integration with LangGraph Agent Tools

The skills are injected into the agent's context, and tools provide execution:

```python
# src/agents/tools/doc_gen_tool.py
from langchain_core.tools import tool
from src.skills._loader import SkillLoader

skill_loader = SkillLoader(Path("src/skills"))

def create_doc_gen_tools(department: str) -> list:
    """Create document generation tools with skill context injection."""

    @tool
    async def generate_pptx(
        topic: str,
        slide_outline: list[dict],
        use_template: str | None = None,
    ) -> str:
        """Generate a PowerPoint presentation.

        The PPTX skill instructions have been loaded into your context.
        Follow the skill's design guidelines, typography rules, and QA process.

        Args:
            topic: The presentation topic/title
            slide_outline: List of slide specs, each with 'title', 'content_type'
                          (bullets|chart|table|image), and 'content' data
            use_template: Optional department template name. If None, creates from scratch.
        """
        # Skill instructions are already in the agent's system prompt
        # The LLM reasons about content and generates the pptxgenjs code
        # This tool executes that code and returns the file path
        template_path = _resolve_template(department, use_template, "pptx")
        output_path = GENERATED_DIR / f"{uuid4()}.pptx"

        if template_path:
            # Edit existing template using unpack → modify → pack workflow
            return await _edit_template_pptx(template_path, slide_outline, output_path)
        else:
            # Create from scratch using pptxgenjs
            return await _create_from_scratch_pptx(topic, slide_outline, department, output_path)

    @tool
    async def generate_docx(
        title: str,
        sections: list[dict],
        use_template: str | None = None,
    ) -> str:
        """Generate a Word document.

        Args:
            title: Document title
            sections: List of section specs with 'heading', 'content', 'level'
            use_template: Optional department template name
        """
        template_path = _resolve_template(department, use_template, "docx")
        output_path = GENERATED_DIR / f"{uuid4()}.docx"

        if template_path:
            return await _edit_template_docx(template_path, sections, output_path)
        else:
            return await _create_from_scratch_docx(title, sections, department, output_path)

    @tool
    async def generate_xlsx(
        title: str,
        sheets: list[dict],
        use_template: str | None = None,
    ) -> str:
        """Generate an Excel spreadsheet.

        Args:
            title: Workbook title
            sheets: List of sheet specs with 'name', 'headers', 'rows', 'formulas', 'charts'
            use_template: Optional department template name
        """
        template_path = _resolve_template(department, use_template, "xlsx")
        output_path = GENERATED_DIR / f"{uuid4()}.xlsx"

        if template_path:
            return await _edit_template_xlsx(template_path, sheets, output_path)
        else:
            return await _create_from_scratch_xlsx(title, sheets, department, output_path)

    return [generate_pptx, generate_docx, generate_xlsx]
```

#### 9.6 Skill Injection into Department Agent System Prompt

When a department agent is activated, the orchestrator checks if document generation is needed and injects the relevant skill:

```python
# src/graph/subgraphs/department_factory.py

async def build_agent_system_prompt(
    department: str,
    config: DepartmentPromptConfig,
    state: AgentState,
) -> str:
    """Build system prompt with skill injection via progressive disclosure."""

    # Base department prompt (always included)
    prompt_parts = [config.system_prompt]

    # Stage 1: Include skill metadata for all skills (~300 tokens total)
    all_skills = skill_loader.get_all_metadata()
    skill_index = "\n".join(
        f"- **{s.name}**: {s.description}" for s in all_skills
    )
    prompt_parts.append(f"\n## Available Document Skills\n{skill_index}")

    # Stage 2: If document generation is needed, inject full skill instructions
    if state.get("requires_document_generation"):
        doc_type = state.get("document_output", {}).get("doc_type")
        if doc_type:
            skill = skill_loader.activate_skill(doc_type)
            prompt_parts.append(
                f"\n## Active Skill: {skill.meta.name}\n{skill.instructions}"
            )

    # Retrieved context
    context = format_context(state["reranked_results"], config.max_context_chunks)
    prompt_parts.append(f"\n## Retrieved Context\n{context}")

    return "\n\n".join(prompt_parts)
```

#### 9.7 Department Formatting Templates

```
src/templates/                       # Department formatting template repository
├── shared/
│   ├── generic_report.docx          # Fallback template any department can use
│   ├── generic_presentation.pptx
│   └── generic_spreadsheet.xlsx
├── hr/
│   ├── policy_report.docx           # Jinja2/OOXML tags for policy docs
│   ├── onboarding_deck.pptx         # Slide layouts: Welcome, Benefits, Policies
│   ├── headcount_tracker.xlsx       # Sheets: Summary, By Department, By Location
│   └── _style.json                  # HR brand: colors, fonts, heading sizes
├── finance/
│   ├── financial_report.docx
│   ├── quarterly_review.pptx
│   ├── budget_template.xlsx         # With formula structures + color coding
│   └── _style.json                  # Finance brand config
├── sales/
│   ├── proposal.docx
│   ├── sales_deck.pptx             # Layouts: Cover, Problem, Solution, Pricing
│   ├── pipeline_tracker.xlsx
│   └── _style.json                  # Sales brand: bold colors, chart-heavy
└── marketing/
    ├── campaign_brief.docx
    ├── brand_deck.pptx              # Brand-consistent layouts
    ├── campaign_metrics.xlsx
    └── _style.json                  # Marketing brand: visual-forward palette
```

Each `_style.json` contains department-specific formatting defaults:
```json
{
  "colors": {
    "primary": "#1E2761",
    "secondary": "#CADCFC",
    "accent": "#F96167"
  },
  "fonts": {
    "heading": "Georgia",
    "body": "Calibri",
    "heading_size": 36,
    "body_size": 14
  },
  "pptx": {
    "default_layout": "dark_sandwich",
    "chart_style": "minimal"
  },
  "xlsx": {
    "header_fill": "D5E8F0",
    "input_font_color": "0000FF",
    "formula_font_color": "000000"
  }
}
```

Template resolution: department-specific → shared → create from scratch.

#### 9.8 QA Verification Loop (from Anthropic Skills)

A critical pattern from the Anthropic PPTX/DOCX skills: **never trust the first render**. The agent runs a QA verification loop:

```
Generate document
  → Convert to images (LibreOffice → PDF → pdftoppm → JPGs)
  → Visual inspection (LLM reviews slide/page images for issues)
  → List issues found
  → Fix issues
  → Re-verify affected pages/slides
  → Repeat until clean pass
```

This is implemented as a sub-loop within the `document_generator` node. The visual QA uses the cheap LLM model to keep costs down — it only needs to identify visual overlap, text overflow, and formatting errors.

#### 9.9 Key Libraries

| Library | Purpose |
|---------|---------|
| `pptxgenjs` (Node.js, via subprocess) | PPTX creation from scratch (higher quality than python-pptx) |
| `python-pptx` | PPTX reading and template editing |
| `docx` (Node.js npm, via subprocess) | DOCX creation from scratch (docx-js) |
| `python-docx` | DOCX reading |
| `openpyxl` | XLSX creation and editing |
| `pandas` | XLSX data analysis and bulk operations |
| `markitdown` | Text extraction from PPTX/DOCX |
| `matplotlib` / `plotly` | Chart image generation |
| LibreOffice (`soffice`) | PDF conversion, formula recalculation, DOCX validation |
| Poppler (`pdftoppm`) | PDF → image for visual QA |

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Vision extraction | GPT-4.1-mini for PPTX + PDF | Renders every page/slide as PNG → vision model captures charts, graphs, diagrams with exact numbers; text parsers miss spatial/visual data. Azure DI OCR loses chart context. Cost acceptable at 250K TPM |
| OCR fallback | Azure Document Intelligence | Fallback for scanned PDFs (no text layer) when vision unavailable; XLSX table augmentation; already on Azure ecosystem |
| LLM for metadata/summarization | GPT-4.1-mini (vision endpoint) | Fast non-reasoning model; avoids gpt-5.2-chat reasoning token overhead that caused 30s+ summarization; shared endpoint with vision extraction |
| Pipeline concurrency | asyncio.gather for metadata + summary + entities | 3 independent LLM/NLP calls run in parallel — wall time = slowest one (~8s) vs sequential (~20s) |
| Embedding concurrency | Up to 4 batches in parallel | Semaphore-limited concurrent API calls; reduces embedding time for large documents by ~60% |
| Embedding dimensions | 1536 (not 3072) | ~98% quality, half storage/memory; Matryoshka allows increasing later without re-embedding |
| RBAC enforcement | Pre-expanded user-access table with JOIN filter | Every query is RBAC-filtered at DB level; pre-expansion enables fast JOINs vs post-filtering |
| Reranker | BGE-reranker-v2-m3 (local) | Zero API cost; benchmarks close to Cohere Rerank; multilingual; ~100ms for 20 candidates |
| HNSW params | m=24, ef_construction=200, ef_search=200 | Optimized for recall over speed per requirements; enterprise corpus may reach 1M+ chunks |
| LLM cost strategy | Cheap model for classification/grading, expensive model only for final reasoning | 2-4 cheap calls + 1-2 expensive calls per request vs naive 6-10 expensive calls |
| Blob staging | Yes, before processing | Decouples sync from processing; enables reprocessing; cheaper than repeated Graph API calls |
| Doc gen architecture | Agent Skills (SKILL.md) + LangGraph tools | Skills provide expert instructions via progressive disclosure (~100 tok metadata, <5K tok full); tools provide execution. Based on [anthropics/skills](https://github.com/anthropics/skills) spec |
| PPTX creation | pptxgenjs (Node.js) for scratch, python-pptx for templates | pptxgenjs produces higher quality complex layouts than python-pptx; template editing uses XML unpack/pack workflow |
| DOCX creation | docx-js (Node.js) for scratch, XML editing for templates | docx-js handles complex layouts (TOC, headers/footers, tables) better than docxtpl; XML editing preserves tracked changes |
| Doc gen QA | Visual QA loop (render → image → LLM inspect → fix) | Anthropic's production skills mandate QA: "Assume there are problems. Your job is to find them." Catches overlap, overflow, formatting errors |

---

## Deployment

**FastAPI + Docker** with Celery for async ingestion:

```
docker-compose.yml
├── api          # FastAPI (query API + WebSocket streaming)
├── worker       # Celery worker (document ingestion pipeline)
├── postgres     # PostgreSQL 16 + pgvector extension
├── neo4j        # Neo4j 5.x
├── redis        # Celery broker + caching layer
└── nginx        # Reverse proxy (optional)
```

- `Dockerfile` for the Python app (shared between api and worker)
- `docker-compose.yml` for local dev with all services
- Celery tasks for: document processing, embedding, graph building, permission re-expansion
- FastAPI endpoints: `/query` (POST), `/query/stream` (WebSocket), `/upload`, `/templates`, `/health`
- Auth middleware validates Entra ID JWT tokens, extracts user_id/groups for RBAC

---

## Configuration

All configuration via `pydantic-settings` with env vars (`.env` file support):

```
# LLM (configurable provider)
LLM_PROVIDER (azure_openai|anthropic)
LLM_EXPENSIVE_MODEL (gpt-4o | claude-sonnet-4-6)
LLM_CHEAP_MODEL (gpt-4o-mini | claude-haiku-4-5-20251001)
AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
ANTHROPIC_API_KEY

# SharePoint
GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SITE_URLS

# Storage
BLOB_ACCOUNT_URL, BLOB_CONTAINER_NAME

# Vision (GPT-4.1-mini for chart/graph extraction from PPTX slides & PDF pages)
VISION_AZURE_ENDPOINT, VISION_AZURE_API_KEY, VISION_AZURE_API_VERSION, VISION_AZURE_DEPLOYMENT

# OCR (fallback for scanned PDFs, XLSX augmentation)
OCR_PROVIDER (azure_di|doctr), OCR_AZURE_ENDPOINT, OCR_AZURE_KEY

# Chunking
CHUNK_TARGET_TOKENS (450), CHUNK_MAX_TOKENS (600), CHUNK_OVERLAP_TOKENS (60)

# Embedding
EMBEDDING_AZURE_ENDPOINT, EMBEDDING_AZURE_DEPLOYMENT (text-embedding-3-large), EMBEDDING_DIMENSIONS (1536)

# Databases
PGVECTOR_CONNECTION_STRING, PGVECTOR_HNSW_EF_SEARCH (200)
NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
REDIS_URL

# Celery
CELERY_BROKER_URL, CELERY_RESULT_BACKEND
```

---

## Implementation Phases

### Phase 1 — Foundation (config, DB, project scaffolding)
1. `pyproject.toml` with all dependencies
2. `config/settings.py` — Pydantic Settings for all subsystems
3. Database schema + Alembic migrations (documents, chunks, permissions, sync_cursors)
4. `PgVectorStore` with HNSW index creation
5. Neo4j driver wrapper + schema creation (constraints, indexes)
6. `AzureOpenAIEmbedder` with batch manager

### Phase 2 — Document Processing (parsers, vision, OCR, metadata, summarization)
7. File-type parsers: PDF → DOCX → PPTX → XLSX
8. `VisionSlideExtractor` — GPT-4.1-mini vision for PPTX slides and PDF pages (charts/graphs/diagrams)
9. `slide_renderer.py` — PPTX → PDF (LibreOffice headless) → PNG (pymupdf)
10. `pdf_renderer.py` — PDF → PNG (pymupdf direct)
11. `AzureDocumentIntelligenceOCR` — fallback for scanned PDFs, XLSX augmentation
12. `MetadataExtractor` (merge Graph API + file properties + LLM via GPT-4.1-mini)
13. `LLMSummarizer` (single-pass + hierarchical map-reduce + per-sheet summaries for XLSX, via GPT-4.1-mini)
14. `DocumentProcessingPipeline` orchestrator — concurrent metadata/summary/entity extraction via asyncio.gather

### Phase 3 — Chunking & Embedding Pipeline
12. `AdaptiveSemanticChunker` with PDF strategy first
13. Remaining strategies (DOCX, PPTX, XLSX)
14. `SemanticBoundaryDetector` (optional enhancement)
15. End-to-end test: file → chunks + embeddings in pgvector

### Phase 4 — SharePoint Integration
16. `GraphClientFactory` + MSAL authentication
17. `SharePointConnector` — document download + metadata
18. `PermissionResolver` — Entra ID RBAC capture + group expansion
19. `DeltaSyncManager` — delta tokens + incremental sync
20. `AzureBlobStore` staging layer

### Phase 5 — Knowledge Graph
21. Neo4j relationship builder (entity extraction, topic classification)
22. Citation/reference extraction
23. Materialized shared-entity/topic relationships
24. Graph traversal queries for retrieval

### Phase 6 — Retrieval Pipeline
25. HyDE reformulation
26. Metadata pre-filter builder (RBAC + dept + date)
27. Vector search (cosine + MMR)
28. Graph search integration
29. BGE reranker integration
30. Relevance grader with retry logic

### Phase 7 — Agentic Framework
31. `AgentState` definition
32. Orchestrator graph (all nodes + conditional edges)
33. Department sub-agent factory
34. All department prompts (HR, Finance, Sales, Marketing, General)
35. Agent tools (search, doc gen, chart)

### Phase 8 — Agent Skills & Document Generation
36. SkillLoader with progressive disclosure (metadata → full content → references)
37. Port/adapt PPTX SKILL.md from anthropics/skills (design guidelines, QA loop, pptxgenjs patterns)
38. Port/adapt DOCX SKILL.md (docx-js creation, XML editing, tracked changes, QA)
39. Port/adapt XLSX SKILL.md (openpyxl + formula recalc, financial model rules, QA)
40. Chart generation SKILL.md (matplotlib/plotly, chart type selection)
41. Visual QA pipeline (LibreOffice → PDF → pdftoppm → LLM image review → fix loop)
42. PPTX/DOCX/XLSX generator implementations (tools that agents invoke)
43. Department template repository with `_style.json` per department
44. LangGraph tool registration: `generate_pptx`, `generate_docx`, `generate_xlsx`
45. Skill injection into agent system prompts (metadata always, full content on-demand)

### Phase 9 — API, Deployment & Integration
41. Dockerfile + docker-compose.yml (api, worker, postgres, neo4j, redis)
42. Celery task definitions for ingestion pipeline
43. FastAPI endpoints (query, stream, upload, template management, health)
44. WebSocket for streaming responses
45. Auth middleware (Entra ID JWT token validation → RBAC context)

### Phase 10 — Testing & Hardening
46. Unit tests per component
47. Integration tests for LangGraph flows
48. Retrieval quality evaluation (precision/recall dataset)
49. Error handling, retry logic, structured logging (`structlog`)

---

## Verification Plan

1. **Ingestion:** Upload a sample set of PDF/PPTX/XLSX/DOCX to a test SharePoint site → run pipeline → verify chunks + embeddings + RBAC entries in pgvector + nodes/relationships in Neo4j
2. **RBAC:** Query as User A (HR access) and User B (Sales access) → verify each only sees their authorized documents
3. **Retrieval Quality:** Create an evaluation dataset of 50+ query-expected_doc pairs → measure precision@k and recall@k → tune HNSW params, reranker, and HyDE threshold
4. **Graph Augmentation:** Query that requires cross-document reasoning → verify Neo4j graph traversal surfaces related documents not found by vector search alone
5. **Department Routing:** Submit queries with clear department signals → verify correct sub-agent is invoked with appropriate prompt/temperature
6. **Document Generation:** Request "create a Q3 sales deck" → verify PPTX generated from sales template with correct data, formatting, and charts
7. **End-to-End:** Full flow from user query → retrieval → agent reasoning → answer with citations (SharePoint links) → optional document output
8. **Open WebUI Integration:** Submit query through Open WebUI chat → verify citations render as clickable pills with SharePoint URLs → verify generated documents appear as downloadable attachments → verify RBAC context propagated from SSO

---

## Section 10: Open WebUI Frontend Integration

### 10.1 Integration Strategy Overview

Replace the custom FastAPI chat frontend with **Open WebUI** as the user-facing interface. Our better-rag backend becomes a headless API that Open WebUI connects to via a **Pipe Function** (Open WebUI's plugin system for custom model providers).

```
┌─────────────────────────────────────────────────────────┐
│                    Open WebUI (Frontend)                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │  Chat UI  │  │ Citation │  │  File Download/Upload │  │
│  │ (Svelte)  │  │  Pills   │  │      Attachments      │  │
│  └─────┬─────┘  └────┬─────┘  └──────────┬────────────┘  │
│        │              │                    │               │
│  ┌─────┴──────────────┴────────────────────┴──────────┐   │
│  │         BetterRAG Pipe Function (Python)            │   │
│  │  • Receives chat messages + user context            │   │
│  │  • Calls better-rag FastAPI backend                 │   │
│  │  • Streams response tokens to Open WebUI            │   │
│  │  • Emits citations via __event_emitter__ (source)   │   │
│  │  • Emits generated files via __event_emitter__      │   │
│  └─────────────────────┬──────────────────────────────┘   │
│                        │ HTTP/SSE                          │
└────────────────────────┼──────────────────────────────────┘
                         │
┌────────────────────────┼──────────────────────────────────┐
│          better-rag FastAPI Backend (Headless)             │
│                        │                                   │
│  ┌─────────────────────┴──────────────────────────────┐   │
│  │              /api/v1/chat/stream (SSE)              │   │
│  │  • Receives query + user_context (from headers)     │   │
│  │  • Runs LangGraph orchestrator                      │   │
│  │  • Streams: tokens, citations, file_urls            │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  LangGraph → Retrieval → Dept Agents → Doc Gen             │
│  pgvector │ Neo4j │ Celery Workers                         │
└────────────────────────────────────────────────────────────┘
```

**Why Pipe Function (not external Pipeline)?** Open WebUI's Pipe Functions run inside the Open WebUI process and have access to `__event_emitter__` — the mechanism for emitting citations and file attachments to the chat UI. External Pipelines (separate server) **do not** support event emitters for citations. This is the critical distinction.

---

### 10.2 Authentication & RBAC Context Flow

```
User Browser
  → Entra ID OIDC login (Open WebUI native SSO)
  → Open WebUI session (JWT with Entra ID claims)
  → Pipe Function receives __user__ dict {id, email, role}
  → Pipe Function extracts Entra ID user_id from __user__ or __oauth_token__
  → HTTP call to better-rag backend with X-User-Id, X-User-Email, X-User-Groups headers
  → Backend resolves user → document_user_access table → RBAC-filtered retrieval
```

**Open WebUI OIDC Configuration** (for Entra ID):
```env
# Open WebUI environment variables
ENABLE_OAUTH_SIGNUP=true
OAUTH_PROVIDER_NAME=microsoft
OAUTH_CLIENT_ID=<app-registration-client-id>
OAUTH_CLIENT_SECRET=<app-registration-secret>
OPENID_PROVIDER_URL=https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration
OAUTH_SCOPES=openid profile email User.Read
ENABLE_OAUTH_GROUP_MANAGEMENT=true
OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true
```

**User context extraction** in the Pipe Function:
```python
# The __user__ dict contains: {id, email, name, role}
# The __oauth_token__ dict contains the Entra ID access token
# We forward both to the backend for RBAC resolution
```

The backend's existing `PermissionResolver` already maps Entra ID user IDs → `document_user_access` table. The only change: instead of validating Entra ID JWT directly, the backend trusts the user context forwarded from Open WebUI (since Open WebUI already validated the OIDC token). This is secured by:
1. The Pipe Function runs inside Open WebUI (trusted process)
2. A shared API key between the Pipe Function and backend (`BETTER_RAG_API_KEY`)
3. The backend is not exposed publicly — only Open WebUI can reach it

---

### 10.3 Pipe Function Implementation

**File:** `src/openwebui/pipe_function.py` (also deployable directly in Open WebUI admin UI)

```python
"""
title: BetterRAG Agent
author: better-rag
version: 1.0.0
description: Enterprise RAG agent with department-specific sub-agents,
  SharePoint document retrieval with RBAC, and PPTX/DOCX/XLSX generation.
"""

import json
import httpx
from pydantic import BaseModel, Field
from typing import AsyncGenerator


class Pipe:
    class Valves(BaseModel):
        BETTER_RAG_API_URL: str = Field(
            default="http://better-rag-api:8000",
            description="URL of the better-rag FastAPI backend",
        )
        BETTER_RAG_API_KEY: str = Field(
            default="",
            description="Shared API key for backend authentication",
        )
        STREAM_TIMEOUT: int = Field(
            default=120,
            description="Timeout in seconds for streaming responses",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self) -> list[dict]:
        """Register as a single model in Open WebUI."""
        return [
            {
                "id": "better-rag-agent",
                "name": "BetterRAG Enterprise Agent",
            }
        ]

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __event_emitter__=None,
        __event_call__=None,
    ) -> AsyncGenerator[str, None]:
        """
        Main pipe function — called for every chat message.

        1. Forwards the query to better-rag backend via SSE stream
        2. Yields response tokens for streaming display
        3. Emits citations as "source" events (renders as pills in UI)
        4. Emits generated files as "files" events (renders as downloads)
        5. Emits status updates during processing
        """
        # Extract user context for RBAC
        user_id = __user__.get("id", "")
        user_email = __user__.get("email", "")

        # Get the latest user message
        messages = body.get("messages", [])
        if not messages:
            yield "No message provided."
            return

        # Emit status: processing started
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {
                    "description": "Analyzing query...",
                    "done": False,
                },
            })

        # Call better-rag backend with SSE streaming
        headers = {
            "Authorization": f"Bearer {self.valves.BETTER_RAG_API_KEY}",
            "X-User-Id": user_id,
            "X-User-Email": user_email,
            "Accept": "text/event-stream",
        }

        payload = {
            "messages": messages,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.valves.STREAM_TIMEOUT
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.valves.BETTER_RAG_API_URL}/api/v1/chat/stream",
                    json=payload,
                    headers=headers,
                ) as response:
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]  # Strip "data: " prefix
                        if data == "[DONE]":
                            break

                        event = json.loads(data)
                        event_type = event.get("type")

                        if event_type == "token":
                            # Stream response text to chat
                            yield event["content"]

                        elif event_type == "status":
                            # Update processing status in UI
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "status",
                                    "data": {
                                        "description": event["description"],
                                        "done": event.get("done", False),
                                    },
                                })

                        elif event_type == "citation":
                            # Emit citation pill in chat UI
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "source",
                                    "data": {
                                        "document": [event["content"]],
                                        "metadata": [
                                            {"source": event["sharepoint_url"]}
                                        ],
                                        "source": {
                                            "name": event["title"],
                                            "url": event["sharepoint_url"],
                                        },
                                    },
                                })

                        elif event_type == "file":
                            # Emit generated file as downloadable attachment
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "files",
                                    "data": {
                                        "files": [
                                            {
                                                "type": "file",
                                                "name": event["filename"],
                                                "url": event["download_url"],
                                                "mime_type": event["mime_type"],
                                            }
                                        ],
                                    },
                                })

                        elif event_type == "error":
                            yield f"\n\n**Error:** {event['message']}"

        except httpx.TimeoutException:
            yield "\n\n**Error:** Request timed out. Please try again."
        except Exception as e:
            yield f"\n\n**Error:** {str(e)}"

        # Emit final status: done
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Complete", "done": True},
            })
```

---

### 10.4 Backend SSE Stream Protocol

The better-rag FastAPI backend streams responses using Server-Sent Events (SSE). Each event is a JSON object prefixed with `data: ` followed by a newline.

**SSE Event Types:**

| Event Type | Payload | Purpose |
|-----------|---------|---------|
| `token` | `{"type": "token", "content": "..."}` | Streamed response text (one token or chunk at a time) |
| `status` | `{"type": "status", "description": "...", "done": bool}` | Processing stage updates (e.g., "Searching documents...", "Analyzing with Finance agent...") |
| `citation` | `{"type": "citation", "title": "...", "content": "...", "sharepoint_url": "..."}` | Source document citation — one per retrieved source |
| `file` | `{"type": "file", "filename": "...", "download_url": "...", "mime_type": "..."}` | Generated document (PPTX/DOCX/XLSX) available for download |
| `error` | `{"type": "error", "message": "..."}` | Error during processing |
| `[DONE]` | (raw string) | Stream termination signal |

**Example stream for a query "Create a Q3 sales report":**

```
data: {"type": "status", "description": "Analyzing query...", "done": false}
data: {"type": "status", "description": "Searching documents (Sales)...", "done": false}
data: {"type": "status", "description": "Generating response with Sales agent...", "done": false}
data: {"type": "token", "content": "Based on the Q3 sales data, here are the key highlights:\n\n"}
data: {"type": "token", "content": "**Revenue:** $4.2M (+12% QoQ)..."}
data: {"type": "citation", "title": "Q3 Sales Pipeline Report.xlsx", "content": "Q3 pipeline data showing $4.2M closed revenue...", "sharepoint_url": "https://contoso.sharepoint.com/sites/sales/Shared%20Documents/Q3_Pipeline.xlsx"}
data: {"type": "citation", "title": "Sales Quarterly Review Deck.pptx", "content": "Slide 4: Revenue breakdown by region...", "sharepoint_url": "https://contoso.sharepoint.com/sites/sales/Shared%20Documents/Q3_Review.pptx"}
data: {"type": "status", "description": "Generating sales report...", "done": false}
data: {"type": "token", "content": "\n\nI've generated a Q3 Sales Report for you — see the attached PPTX file."}
data: {"type": "file", "filename": "Q3_Sales_Report.pptx", "download_url": "/api/v1/files/generated/abc123.pptx", "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
data: {"type": "status", "description": "Complete", "done": true}
data: [DONE]
```

**FastAPI SSE Endpoint:**

```python
# src/main.py — replaces the original /query endpoint with SSE streaming

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from src.graph.orchestrator import run_orchestrator

@app.post("/api/v1/chat/stream")
async def chat_stream(request: Request):
    body = await request.json()
    user_id = request.headers.get("X-User-Id")
    user_email = request.headers.get("X-User-Email")

    async def event_generator():
        async for event in run_orchestrator(
            messages=body["messages"],
            user_id=user_id,
            user_email=user_email,
        ):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

---

### 10.5 Citation Rendering in Open WebUI

Open WebUI renders citations emitted via `__event_emitter__({"type": "source", ...})` as **clickable pills** at the bottom of the assistant's message. Each pill shows the source name and links to the URL.

**Our citation mapping:**

| better-rag field | Open WebUI citation field | Description |
|-----------------|--------------------------|-------------|
| `chunk.content` (snippet) | `data.document[0]` | Text displayed when user clicks the citation pill |
| `chunk.sharepoint_url` | `data.metadata[0].source` | URL used for the "Open in SharePoint" action |
| `document.title` | `data.source.name` | Displayed as the pill label |
| `chunk.sharepoint_url` | `data.source.url` | Hyperlink target for the pill |

**How it works in the LangGraph orchestrator:**
After the `response_synthesizer` node assembles the final answer, the `reranked_results` in `AgentState` contain the chunks that were used. The SSE stream emits one `citation` event per unique source document:

```python
# In the orchestrator's stream generator
seen_docs = set()
for chunk in state["reranked_results"]:
    doc_id = chunk["document_id"]
    if doc_id not in seen_docs:
        seen_docs.add(doc_id)
        yield {
            "type": "citation",
            "title": chunk["document_title"],
            "content": chunk["content"][:500],  # Preview snippet
            "sharepoint_url": chunk["sharepoint_url"],
        }
```

**Inline references:** The department agent system prompts instruct the LLM to reference sources inline using `[Source Title]` format in the response text. The citation pills at the bottom provide the clickable links. This matches Open WebUI's native RAG citation UX where inline `[source_id]` markers correspond to numbered pills.

---

### 10.6 Generated File Delivery

When the LangGraph agent generates a document (PPTX/DOCX/XLSX), the file must be served to Open WebUI for download. Two mechanisms:

#### Option A: Serve from better-rag backend (simpler)

Generated files are stored in a `/generated/` directory served by the FastAPI backend. The `download_url` in the SSE stream is a direct URL to the file:

```python
# src/main.py
from fastapi.staticfiles import StaticFiles

app.mount("/api/v1/files/generated", StaticFiles(directory="generated"), name="generated")
```

The Pipe Function emits:
```python
await __event_emitter__({
    "type": "files",
    "data": {
        "files": [{
            "type": "file",
            "name": "Q3_Sales_Report.pptx",
            "url": f"{self.valves.BETTER_RAG_API_URL}/api/v1/files/generated/abc123.pptx",
            "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }]
    }
})
```

#### Option B: Upload to Open WebUI's file storage (better UX)

The Pipe Function downloads the generated file from the backend and uploads it to Open WebUI's own file storage via internal API, so the file persists in the chat history:

```python
import requests
from open_webui.models.files import Files

# Download from backend
file_bytes = await client.get(event["download_url"])

# Upload to Open WebUI's file storage
file_record = Files.insert_new_file(
    user_id=__user__["id"],
    filename=event["filename"],
    content_type=event["mime_type"],
    content=file_bytes.content,
)

# Emit as chat attachment
await __event_emitter__({
    "type": "files",
    "data": {
        "files": [{
            "type": "file",
            "id": file_record.id,
            "name": event["filename"],
            "url": f"/api/v1/files/{file_record.id}/content",
            "mime_type": event["mime_type"],
        }]
    }
})
```

**Recommendation:** Start with Option A for simplicity, migrate to Option B when the full Open WebUI file management integration is needed.

---

### 10.7 Status Updates During Processing

The LangGraph orchestrator emits status events at each processing stage. These appear as progress indicators above the chat message in Open WebUI's UI.

**Status progression for a typical query:**

| Stage | Status Description |
|-------|-------------------|
| Query Analysis | "Analyzing your query..." |
| Smart Router | "Routing to {department} agent..." |
| HyDE (if needed) | "Reformulating query..." |
| Vector Search | "Searching documents..." |
| Graph Expansion | "Finding related documents..." |
| Reranking | "Ranking results..." |
| Department Agent | "Generating response with {department} agent..." |
| Document Generation (if needed) | "Creating {doc_type} document..." |
| Visual QA (if doc gen) | "Verifying document quality..." |
| Complete | "Complete" (done=true, hidden=true) |

The final status uses `done: true` which Open WebUI automatically hides after a brief display.

---

### 10.8 Bypassing Open WebUI's Built-in RAG

Open WebUI has its own RAG system. Since we're using our own retrieval pipeline, we need to **bypass** Open WebUI's built-in RAG to avoid double-retrieval.

**Approach:** The Pipe Function acts as a complete model replacement — it receives the raw user message and handles everything. Open WebUI's built-in RAG only activates when documents are uploaded directly to Open WebUI's knowledge base (via the `#` tag or drag-and-drop). Since our documents are in SharePoint/pgvector, Open WebUI's RAG never triggers.

If a user uploads a file directly in the chat (drag-and-drop), the Pipe Function receives it via the `__files__` parameter and can forward it to the backend for processing:

```python
async def pipe(self, body: dict, __user__: dict, __files__: list = None, ...):
    if __files__:
        # Forward uploaded files to backend for on-the-fly processing
        for file in __files__:
            # Upload to better-rag backend for indexing or in-context use
            pass
```

---

### 10.9 Docker Compose — Updated with Open WebUI

```yaml
# docker-compose.yml (updated)
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    ports:
      - "3000:8080"
    environment:
      # Disable built-in Ollama (we use our own backend)
      OLLAMA_BASE_URL: ""
      # Entra ID OIDC SSO
      ENABLE_OAUTH_SIGNUP: "true"
      OAUTH_PROVIDER_NAME: microsoft
      OAUTH_CLIENT_ID: ${OAUTH_CLIENT_ID}
      OAUTH_CLIENT_SECRET: ${OAUTH_CLIENT_SECRET}
      OPENID_PROVIDER_URL: https://login.microsoftonline.com/${GRAPH_TENANT_ID}/v2.0/.well-known/openid-configuration
      OAUTH_SCOPES: "openid profile email User.Read"
      ENABLE_OAUTH_GROUP_MANAGEMENT: "true"
      OAUTH_MERGE_ACCOUNTS_BY_EMAIL: "true"
      # Forward user info to pipes
      ENABLE_FORWARD_USER_INFO_HEADERS: "true"
      # Disable default models (only show our BetterRAG pipe)
      DEFAULT_MODELS: "better-rag-agent"
    volumes:
      - open-webui-data:/app/backend/data
    depends_on:
      - api

  api:
    build: .
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000
    environment:
      - BETTER_RAG_API_KEY=${BETTER_RAG_API_KEY}
    env_file: .env
    depends_on:
      - postgres
      - neo4j
      - redis

  worker:
    build: .
    command: celery -A src.celery_app worker --loglevel=info
    env_file: .env
    depends_on:
      - postgres
      - neo4j
      - redis

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: betterrag
      POSTGRES_USER: ${PGVECTOR_USER}
      POSTGRES_PASSWORD: ${PGVECTOR_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: ${NEO4J_USER}/${NEO4J_PASSWORD}
    volumes:
      - neo4jdata:/data
    ports:
      - "7687:7687"
      - "7474:7474"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  open-webui-data:
  pgdata:
  neo4jdata:
```

---

### 10.10 Pipe Function Deployment

The Pipe Function can be deployed in two ways:

**Option A: Admin UI paste** (easiest for development)
1. Open WebUI Admin → Functions → Add Function
2. Paste the content of `src/openwebui/pipe_function.py`
3. Configure Valves: set `BETTER_RAG_API_URL` and `BETTER_RAG_API_KEY`
4. Enable the function — it appears as "BetterRAG Enterprise Agent" in the model selector

**Option B: Volume mount** (production, version-controlled)
Mount the function file into Open WebUI's functions directory:
```yaml
open-webui:
  volumes:
    - ./src/openwebui/pipe_function.py:/app/backend/data/functions/better_rag_agent.py
```

**Option C: API deployment** (CI/CD)
Use Open WebUI's API to create/update the function programmatically:
```bash
curl -X POST http://localhost:3000/api/v1/functions/create \
  -H "Authorization: Bearer $OWUI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "better_rag_agent",
    "name": "BetterRAG Enterprise Agent",
    "type": "pipe",
    "content": "$(cat src/openwebui/pipe_function.py)"
  }'
```

---

### 10.11 Open WebUI Configuration Additions

```env
# .env additions for Open WebUI integration
OPEN_WEBUI_URL=http://open-webui:8080
BETTER_RAG_API_KEY=<shared-secret-for-backend-auth>

# Entra ID OIDC (shared between Open WebUI and backend)
OAUTH_CLIENT_ID=<app-registration-client-id>
OAUTH_CLIENT_SECRET=<app-registration-secret>
GRAPH_TENANT_ID=<azure-ad-tenant-id>
```

---

### 10.12 Updated Project Structure

Add the Open WebUI integration layer:
```
src/
├── openwebui/
│   ├── pipe_function.py              # Main Pipe Function (deployed to Open WebUI)
│   ├── filter_function.py            # Optional: inlet filter for query preprocessing
│   └── action_function.py            # Optional: action buttons (e.g., "Regenerate as DOCX")
├── api/
│   ├── routes/
│   │   ├── chat.py                   # /api/v1/chat/stream (SSE endpoint)
│   │   ├── files.py                  # /api/v1/files/generated/* (file serving)
│   │   └── health.py                 # /health
│   ├── middleware/
│   │   └── auth.py                   # API key + user header validation
│   └── sse.py                        # SSE event formatting utilities
```

---

### 10.13 Key Design Decisions (Open WebUI Integration)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Integration type | Pipe Function (not external Pipeline) | Pipe Functions have `__event_emitter__` access for citations and file attachments; external Pipelines do not |
| Citation delivery | EventEmitter `"source"` type with SharePoint URLs | Renders as native Open WebUI citation pills; clicking opens SharePoint; content preview on hover |
| File delivery | EventEmitter `"files"` type + backend static file serving | Generated PPTX/DOCX/XLSX appear as downloadable attachments in chat |
| Auth flow | Entra ID OIDC → Open WebUI SSO → user headers to backend | Single SSO; Open WebUI validates OIDC; backend trusts forwarded user context |
| RBAC bridge | Pipe Function forwards Entra ID user_id via headers | Backend's existing `document_user_access` table handles all RBAC filtering |
| Built-in RAG | Bypassed (not disabled) | Our Pipe Function handles all retrieval; Open WebUI RAG only activates for direct uploads |
| Streaming | SSE from backend → async generator in Pipe → Open WebUI WebSocket | Token-by-token streaming with interleaved status/citation/file events |

---

### 10.14 Updated Implementation Phases

Insert after Phase 9:

### Phase 9B — Open WebUI Integration
46. Pipe Function implementation (`src/openwebui/pipe_function.py`)
47. Backend SSE streaming endpoint (`/api/v1/chat/stream`)
48. Citation emission mapping (reranked_results → `source` events with SharePoint URLs)
49. Generated file serving (static file mount + `files` event emission)
50. Open WebUI OIDC configuration (Entra ID SSO)
51. Docker Compose update (add `open-webui` service)
52. Status event emission from LangGraph orchestrator nodes
53. End-to-end test: Open WebUI → Pipe → Backend → citations + file downloads

### Phase 10 (Updated) — Testing & Hardening
54. Unit tests per component
55. Integration tests for LangGraph flows
56. Open WebUI integration tests (citation rendering, file downloads, RBAC)
57. Retrieval quality evaluation (precision/recall dataset)
58. Error handling, retry logic, structured logging (`structlog`)

---

## Section 11: Scaling Architecture — Redis + Celery + LangGraph for 800 Users

### 11.1 Scaling Problem Statement

The Section 10 architecture runs LangGraph **synchronously inside the FastAPI process** — each SSE connection holds a thread/coroutine for 5-30 seconds while the orchestrator executes multiple LLM calls, vector searches, and optionally generates documents (30-60s). This doesn't scale to 800 users.

**Concurrency math for 800 users:**
- Assume 15-20% peak concurrency → **120-160 simultaneous requests**
- Average query: ~8 seconds (query analysis + retrieval + rerank + agent reasoning)
- Document generation: ~30-60 seconds, but only ~5% of requests
- At peak: ~120 query requests + ~8 doc-gen requests running simultaneously

A single FastAPI process with in-process LangGraph execution would bottleneck at ~10-20 concurrent requests due to LLM API latency, CPU-bound reranking (BGE model), and connection pool limits.

---

### 11.2 Scaled Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Open WebUI (2-3 replicas)                     │
│  Entra ID SSO │ WebSocket via Redis │ PostgreSQL shared state    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              BetterRAG Pipe Function                      │   │
│  │  → HTTP/SSE to load balancer → FastAPI API servers        │   │
│  └──────────────────────┬───────────────────────────────────┘   │
└─────────────────────────┼───────────────────────────────────────┘
                          │
            ┌─────────────┼─────────────┐
            │     Load Balancer / DNS    │
            └─────────────┼─────────────┘
                          │
┌─────────────────────────┼───────────────────────────────────────┐
│            FastAPI API Servers (3 replicas)                       │
│            Lightweight SSE gateway — NO LangGraph execution       │
│                                                                   │
│  For each request:                                                │
│  1. Generate unique stream_key = "rag:stream:{request_id}"       │
│  2. Dispatch Celery task (query or doc_gen queue)                 │
│  3. XREAD on Redis Stream (block until events arrive)             │
│  4. Yield events as SSE to the Pipe Function                     │
│  5. Cleanup: XTRIM the stream after [DONE]                       │
└───────────────────┬──────────────────┬──────────────────────────┘
                    │                  │
              Celery dispatch      Redis XREAD
                    │                  │
                    ▼                  │
┌───────────────────────────────┐      │
│           Redis 7+            │◄─────┘
│                               │
│  db=0: Open WebUI (WS, sessions)
│  db=1: Celery broker (task queues)
│  db=2: Redis Streams (SSE bridge)
│  db=3: Cache (HyDE, RBAC, query)
│  db=4: LangGraph checkpoints
│                               │
│  Key data structures:         │
│  • Lists: Celery task queues  │
│  • Streams: per-request SSE   │
│  • Strings: cache entries     │
│  • JSON: LangGraph state      │
└───────────────────────────────┘
          ▲               ▲
          │               │
    XADD events    LangGraph checkpoint
          │               │
┌─────────┴───────────────┴─────────────────────────────────────┐
│                    Celery Workers                               │
│                                                                 │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐  │
│  │  query-worker (×3)      │  │  docgen-worker (×2)         │  │
│  │  Queue: rag.query       │  │  Queue: rag.doc_gen         │  │
│  │  Concurrency: 10/worker │  │  Concurrency: 2/worker      │  │
│  │  = 30 concurrent queries│  │  = 4 concurrent generations │  │
│  │                         │  │                             │  │
│  │  Runs LangGraph:        │  │  Runs LangGraph:            │  │
│  │  • query_analyzer       │  │  • doc_generator node       │  │
│  │  • smart_router         │  │  • visual QA loop           │  │
│  │  • retrieval pipeline   │  │  • file upload              │  │
│  │  • dept agent reasoning │  │                             │  │
│  │  • response synthesis   │  │  Heavy: LibreOffice, pptxgen│  │
│  │                         │  │  Node.js, image rendering   │  │
│  │  XADDs events to stream │  │  XADDs events to stream     │  │
│  └─────────────────────────┘  └─────────────────────────────┘  │
│                                                                 │
│  ┌─────────────────────────┐                                    │
│  │  ingestion-worker (×1)  │                                    │
│  │  Queue: rag.ingestion   │                                    │
│  │  Concurrency: 4         │                                    │
│  │                         │                                    │
│  │  SharePoint sync,       │                                    │
│  │  OCR, chunking,         │                                    │
│  │  embedding, graph build │                                    │
│  └─────────────────────────┘                                    │
└─────────────────────────────────────────────────────────────────┘
          │                    │
          ▼                    ▼
┌──────────────────┐  ┌──────────────────┐
│ PostgreSQL 16    │  │     Neo4j 5      │
│ + pgvector       │  │ Knowledge Graph  │
│                  │  │                  │
│ Pool: 150 max    │  │ Pool: 50 max     │
│ (shared across   │  │                  │
│  all workers)    │  │                  │
└──────────────────┘  └──────────────────┘
```

**Key insight:** The FastAPI API server does **zero** LangGraph execution. It's a lightweight SSE gateway that dispatches work to Celery workers and relays events from Redis Streams. This means API servers can handle thousands of concurrent SSE connections with minimal resources.

---

### 11.3 Redis Streams as the SSE Bridge

The core scaling pattern: **Celery workers publish events to per-request Redis Streams; FastAPI API servers consume them and relay as SSE.**

```python
# src/api/routes/chat.py — Updated SSE endpoint with Celery + Redis Streams

import json
import uuid
import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from src.celery_app import run_query_task

redis_client = aioredis.from_url("redis://redis:6379/2")  # db=2 for streams

@app.post("/api/v1/chat/stream")
async def chat_stream(request: Request):
    body = await request.json()
    user_id = request.headers.get("X-User-Id")
    user_email = request.headers.get("X-User-Email")

    # 1. Create unique stream key for this request
    request_id = str(uuid.uuid4())
    stream_key = f"rag:stream:{request_id}"

    # 2. Dispatch LangGraph execution to Celery worker
    run_query_task.apply_async(
        kwargs={
            "messages": body["messages"],
            "user_id": user_id,
            "user_email": user_email,
            "stream_key": stream_key,
        },
        queue="rag.query",
    )

    # 3. Subscribe to Redis Stream and yield SSE events
    async def event_generator():
        last_id = "0-0"
        try:
            while True:
                # Block for up to 30s waiting for new events
                results = await redis_client.xread(
                    {stream_key: last_id},
                    count=10,
                    block=30000,  # 30s timeout
                )
                if not results:
                    # Timeout — send keepalive
                    yield ": keepalive\n\n"
                    continue

                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        event_data = fields[b"data"].decode()

                        if event_data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            return  # End stream

                        yield f"data: {event_data}\n\n"
        finally:
            # Cleanup: delete the stream after consumption
            await redis_client.delete(stream_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

```python
# src/celery_app.py — Celery task that runs LangGraph and publishes to Redis Streams

import redis
from celery import Celery

celery_app = Celery("better-rag")
celery_app.config_from_object("config.celery_config")

redis_streams = redis.from_url("redis://redis:6379/2")  # Sync client for workers

@celery_app.task(bind=True, queue="rag.query", acks_late=True)
def run_query_task(self, messages, user_id, user_email, stream_key):
    """Execute LangGraph orchestrator and publish events to Redis Stream."""
    import asyncio
    asyncio.run(_run_query_async(messages, user_id, user_email, stream_key))


async def _run_query_async(messages, user_id, user_email, stream_key):
    """Async wrapper for LangGraph execution with Redis Stream publishing."""
    from src.graph.orchestrator import run_orchestrator
    import redis.asyncio as aioredis

    stream_client = aioredis.from_url("redis://redis:6379/2")

    try:
        async for event in run_orchestrator(
            messages=messages,
            user_id=user_id,
            user_email=user_email,
        ):
            # Publish each event to the Redis Stream
            await stream_client.xadd(
                stream_key,
                {"data": json.dumps(event)},
                maxlen=1000,  # Cap stream length for safety
            )

        # Signal stream completion
        await stream_client.xadd(stream_key, {"data": "[DONE]"})

        # Set TTL on stream for cleanup safety (if consumer dies)
        await stream_client.expire(stream_key, 300)  # 5 min TTL

    except Exception as e:
        # Publish error event
        await stream_client.xadd(
            stream_key,
            {"data": json.dumps({"type": "error", "message": str(e)})},
        )
        await stream_client.xadd(stream_key, {"data": "[DONE]"})
    finally:
        await stream_client.aclose()
```

**Why Redis Streams (not Pub/Sub)?**
- Pub/Sub is fire-and-forget — if the consumer isn't listening when the event fires, it's lost
- Streams are persistent — events accumulate until consumed, so no race condition between task dispatch and consumer subscription
- Streams support consumer groups for future multi-consumer patterns
- Built-in backpressure via `MAXLEN`
- Auto-cleanup via `EXPIRE` + consumer `DELETE`

---

### 11.4 LangGraph Checkpointing with Redis

Use `AsyncRedisSaver` from `langgraph-checkpoint-redis` for fault-tolerant agent execution. If a worker crashes mid-execution, the checkpoint allows resumption from the last completed node.

```python
# src/graph/orchestrator.py — Updated with Redis checkpointing

from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.graph import StateGraph

async def create_orchestrator():
    """Build the LangGraph orchestrator with Redis checkpointing."""
    checkpointer = AsyncRedisSaver.from_conn_string(
        "redis://redis:6379/4",  # db=4 for checkpoints
    )

    graph = StateGraph(AgentState)
    # ... add nodes and edges ...
    return graph.compile(checkpointer=checkpointer)


async def run_orchestrator(messages, user_id, user_email):
    """Execute the orchestrator and yield events."""
    app = await create_orchestrator()

    config = {
        "configurable": {
            "thread_id": f"{user_id}:{uuid4()}",  # Unique per request
        }
    }

    initial_state = {
        "messages": messages,
        "user_context": {
            "user_id": user_id,
            "user_email": user_email,
        },
    }

    # Stream events from LangGraph execution
    async for event in app.astream(initial_state, config=config, stream_mode="updates"):
        for node_name, node_output in event.items():
            # Emit status updates per node
            yield {
                "type": "status",
                "description": _node_status_message(node_name),
                "done": False,
            }

            # If node produces tokens (dept agent), stream them
            if "answer_tokens" in node_output:
                for token in node_output["answer_tokens"]:
                    yield {"type": "token", "content": token}

            # If node produces citations
            if "citations" in node_output:
                for citation in node_output["citations"]:
                    yield {
                        "type": "citation",
                        "title": citation["title"],
                        "content": citation["content"],
                        "sharepoint_url": citation["sharepoint_url"],
                    }

            # If node produces a generated file
            if "generated_file" in node_output:
                yield {
                    "type": "file",
                    "filename": node_output["generated_file"]["filename"],
                    "download_url": node_output["generated_file"]["url"],
                    "mime_type": node_output["generated_file"]["mime_type"],
                }
```

**Checkpoint benefits at scale:**
- Worker crash recovery: resume from last node, not restart from scratch
- Useful for long doc-gen tasks (30-60s) where losing work is expensive
- Enables future "conversation memory" — resume multi-turn conversations
- Performance: Redis checkpoint get = 0.34ms (12x faster than PostgreSQL)

---

### 11.5 Celery Worker Configuration

```python
# config/celery_config.py

broker_url = "redis://redis:6379/1"       # db=1 for Celery broker
result_backend = "redis://redis:6379/1"    # Results in same db (minimal use)

# Task routing — separate queues by workload type
task_routes = {
    "src.celery_app.run_query_task": {"queue": "rag.query"},
    "src.celery_app.run_docgen_task": {"queue": "rag.doc_gen"},
    "src.celery_app.run_ingestion_task": {"queue": "rag.ingestion"},
    "src.celery_app.run_permission_refresh": {"queue": "rag.ingestion"},
    "src.celery_app.run_embedding_batch": {"queue": "rag.ingestion"},
    "src.celery_app.run_graph_build": {"queue": "rag.ingestion"},
}

# Worker prefetch — set to 1 for long-running tasks to avoid starvation
worker_prefetch_multiplier = 1

# Acknowledge after completion (not before) for reliability
task_acks_late = True

# Reject and requeue tasks if worker is killed
task_reject_on_worker_lost = True

# Timeouts
task_soft_time_limit = 120   # 2 min soft limit for queries
task_time_limit = 180        # 3 min hard kill for queries

# Serialization
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]

# Disable result storage for streaming tasks (we use Redis Streams instead)
task_ignore_result = True
```

**Worker launch commands (docker-compose):**

```yaml
# docker-compose.yml — Worker services

  query-worker:
    build: .
    command: >
      celery -A src.celery_app worker
      --queues=rag.query
      --concurrency=10
      --pool=gevent
      --loglevel=info
      --hostname=query-worker@%h
    env_file: .env
    deploy:
      replicas: 3    # 3 workers × 10 concurrency = 30 concurrent queries
    depends_on:
      - postgres
      - neo4j
      - redis

  docgen-worker:
    build: .
    command: >
      celery -A src.celery_app worker
      --queues=rag.doc_gen
      --concurrency=2
      --pool=prefork
      --loglevel=info
      --hostname=docgen-worker@%h
    env_file: .env
    deploy:
      replicas: 2    # 2 workers × 2 concurrency = 4 concurrent doc-gen
    depends_on:
      - postgres
      - redis

  ingestion-worker:
    build: .
    command: >
      celery -A src.celery_app worker
      --queues=rag.ingestion
      --concurrency=4
      --pool=prefork
      --loglevel=info
      --hostname=ingestion-worker@%h
    env_file: .env
    deploy:
      replicas: 1
    depends_on:
      - postgres
      - neo4j
      - redis
```

**Worker pool selection:**
- `query-worker`: `gevent` pool (greenlet-based) — ideal for I/O-bound LLM API calls. 10 greenlets per worker handles concurrent API waits efficiently.
- `docgen-worker`: `prefork` pool (process-based) — document generation uses CPU-heavy subprocess calls (LibreOffice, pptxgenjs, pdftoppm). Processes prevent GIL contention.
- `ingestion-worker`: `prefork` pool — OCR, embedding batch calls, and graph building are CPU+I/O mixed.

---

### 11.6 Redis Database Layout

Single Redis instance, logically partitioned by database number:

| DB | Purpose | Key Pattern | TTL | Est. Memory |
|----|---------|-------------|-----|-------------|
| 0 | Open WebUI (WebSocket coordination, sessions) | `owui:*` | Varies | ~50MB |
| 1 | Celery broker + results | `celery-task-meta-*`, `_kombu.*` | Task-dependent | ~100MB |
| 2 | Redis Streams (SSE bridge) | `rag:stream:{request_id}` | 5 min | ~200MB peak |
| 3 | Application cache | `cache:hyde:{hash}`, `cache:rbac:{user_id}`, `cache:query:{hash}` | 2-60 min | ~500MB |
| 4 | LangGraph checkpoints | `langgraph:*` | 1 hour | ~300MB |

**Total estimated Redis memory at peak: ~1.2GB** — comfortable on a 4GB Redis instance.

**Redis configuration for 800 users:**
```conf
# redis.conf
maxmemory 4gb
maxmemory-policy allkeys-lru
maxclients 10000
timeout 1800
tcp-keepalive 300

# Persistence (optional — streams and cache are ephemeral)
save ""
appendonly no
```

---

### 11.7 Connection Pooling

```python
# config/settings.py — Connection pool configuration

# PostgreSQL (pgvector)
PGVECTOR_POOL_SIZE = 20          # Per API server / per worker process
PGVECTOR_POOL_MAX_OVERFLOW = 10  # Burst capacity
PGVECTOR_MAX_CONNECTIONS = 150   # PostgreSQL max_connections limit
# 3 API servers × 20 = 60, 3 query workers × 10 processes × 5 = 150
# Total: ~210 — set PostgreSQL max_connections = 250

# Neo4j
NEO4J_MAX_CONNECTION_POOL_SIZE = 50  # Shared across workers
NEO4J_CONNECTION_ACQUISITION_TIMEOUT = 30  # seconds

# Redis (per db)
REDIS_MAX_CONNECTIONS = 50  # Per connection pool instance

# LLM API
LLM_RATE_LIMIT_RPM = 500       # Azure OpenAI requests/minute
LLM_RATE_LIMIT_TPM = 150000    # Azure OpenAI tokens/minute
LLM_CONCURRENT_REQUESTS = 50    # Max parallel LLM API calls
```

---

### 11.8 Caching Strategy

```python
# src/storage/cache.py — Redis caching layer

import hashlib
import json
import redis.asyncio as aioredis

cache = aioredis.from_url("redis://redis:6379/3")


async def cache_hyde_embedding(query: str, embedding: list[float], ttl: int = 3600):
    """Cache HyDE-generated embedding for repeated/similar queries."""
    key = f"cache:hyde:{hashlib.sha256(query.encode()).hexdigest()}"
    await cache.setex(key, ttl, json.dumps(embedding))


async def get_cached_hyde_embedding(query: str) -> list[float] | None:
    key = f"cache:hyde:{hashlib.sha256(query.encode()).hexdigest()}"
    result = await cache.get(key)
    return json.loads(result) if result else None


async def cache_user_rbac(user_id: str, document_ids: set[str], ttl: int = 300):
    """Cache user's accessible document IDs for 5 min (avoids repeated JOINs)."""
    key = f"cache:rbac:{user_id}"
    await cache.setex(key, ttl, json.dumps(list(document_ids)))


async def get_cached_user_rbac(user_id: str) -> set[str] | None:
    key = f"cache:rbac:{user_id}"
    result = await cache.get(key)
    return set(json.loads(result)) if result else None


async def cache_query_result(query_hash: str, result: dict, ttl: int = 120):
    """Deduplicate identical queries within 2-min window."""
    key = f"cache:query:{query_hash}"
    await cache.setex(key, ttl, json.dumps(result))
```

**Cache hit rates and impact (estimated):**

| Cache | Expected Hit Rate | Impact |
|-------|-------------------|--------|
| HyDE embedding | 10-15% | Saves 1 LLM call + 1 embedding call per hit |
| User RBAC | 80-90% (same user, 5-min TTL) | Saves 1 JOIN query per hit |
| Query dedup | 5-10% | Saves entire pipeline re-execution |
| BGE reranker model | N/A (keep model loaded in worker memory) | Avoids cold-start model loading |

---

### 11.9 Document Generation Task Separation

Document generation is the heaviest operation (30-60s). It gets its own Celery queue and worker pool to prevent blocking query workers.

```python
# src/celery_app.py — Document generation task

@celery_app.task(
    bind=True,
    queue="rag.doc_gen",
    acks_late=True,
    soft_time_limit=300,   # 5 min soft limit
    time_limit=600,        # 10 min hard kill
)
def run_docgen_task(self, doc_spec, user_id, stream_key):
    """
    Run document generation in a dedicated worker pool.

    Separated from query workers because:
    1. CPU-heavy (LibreOffice, pptxgenjs, image rendering)
    2. Long-running (30-60s with QA loop)
    3. Different concurrency needs (2 per worker vs 10 for queries)
    4. Requires Node.js, LibreOffice, Poppler (different container deps)
    """
    import asyncio
    asyncio.run(_run_docgen_async(doc_spec, user_id, stream_key))
```

**Two-phase flow for queries that need doc generation:**
1. Query worker runs LangGraph up to the department agent → produces the answer + doc generation spec
2. Query worker dispatches `run_docgen_task` to the doc_gen queue with the spec
3. Query worker emits answer tokens and citations to the stream immediately
4. Doc-gen worker generates the file and emits the `file` event to the same stream
5. User sees the answer first, then the file attachment appears moments later

This means the user gets their text answer in ~8 seconds, and the generated document follows in ~30-60 seconds — no blocking.

---

### 11.10 Open WebUI Scaling (Multi-Replica)

Open WebUI itself needs scaling for 800 users. Based on Open WebUI's official scaling guide:

```yaml
# docker-compose.yml — Open WebUI multi-replica

  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    deploy:
      replicas: 3
    environment:
      # PostgreSQL for shared state (NOT SQLite)
      DATABASE_URL: postgresql://${PGVECTOR_USER}:${PGVECTOR_PASSWORD}@postgres:5432/openwebui
      DATABASE_POOL_SIZE: 15
      DATABASE_POOL_MAX_OVERFLOW: 20
      # Redis for WebSocket + session coordination
      REDIS_URL: redis://redis:6379/0
      WEBSOCKET_MANAGER: redis
      ENABLE_WEBSOCKET_SUPPORT: "true"
      # pgvector as Open WebUI's vector DB (for its own RAG, if used)
      VECTOR_DB: pgvector
      PGVECTOR_DB_URL: postgresql://${PGVECTOR_USER}:${PGVECTOR_PASSWORD}@postgres:5432/openwebui_vectors
      # Shared file storage
      STORAGE_PROVIDER: s3
      S3_BUCKET_NAME: ${S3_BUCKET_NAME}
      S3_REGION_NAME: ${S3_REGION_NAME}
      # Workers
      UVICORN_WORKERS: 1  # Let orchestrator handle scaling
      ENABLE_DB_MIGRATIONS: "false"  # Only one replica runs migrations
      # Thread pool for high concurrency
      THREAD_POOL_SIZE: 500
      # SSO
      ENABLE_OAUTH_SIGNUP: "true"
      OAUTH_PROVIDER_NAME: microsoft
      OAUTH_CLIENT_ID: ${OAUTH_CLIENT_ID}
      OAUTH_CLIENT_SECRET: ${OAUTH_CLIENT_SECRET}
      OPENID_PROVIDER_URL: https://login.microsoftonline.com/${GRAPH_TENANT_ID}/v2.0/.well-known/openid-configuration
      OAUTH_SCOPES: "openid profile email User.Read"
      WEBUI_SECRET_KEY: ${WEBUI_SECRET_KEY}  # MUST be identical on all replicas
      DEFAULT_MODELS: "better-rag-agent"
    depends_on:
      - api
      - postgres
      - redis

  # Migration runner (runs once, then exits)
  open-webui-migrate:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      DATABASE_URL: postgresql://${PGVECTOR_USER}:${PGVECTOR_PASSWORD}@postgres:5432/openwebui
      ENABLE_DB_MIGRATIONS: "true"
    command: python -c "from open_webui.apps.webui.main import app; print('Migrations complete')"
    depends_on:
      - postgres
```

---

### 11.11 Full Production Docker Compose

```yaml
# docker-compose.production.yml

services:
  # === Frontend ===
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    deploy:
      replicas: 3
      resources:
        limits: { cpus: "2", memory: 4G }
    # ... (config from 11.10)

  # === API Gateway ===
  api:
    build: .
    command: >
      uvicorn src.main:app --host 0.0.0.0 --port 8000
      --workers 4 --loop uvloop --http httptools
    deploy:
      replicas: 3
      resources:
        limits: { cpus: "2", memory: 2G }
    env_file: .env
    depends_on: [postgres, neo4j, redis]

  # === Celery Workers ===
  query-worker:
    build: .
    command: >
      celery -A src.celery_app worker
      --queues=rag.query --concurrency=10 --pool=gevent
      --hostname=query@%h
    deploy:
      replicas: 3
      resources:
        limits: { cpus: "4", memory: 8G }
    env_file: .env
    depends_on: [postgres, neo4j, redis]

  docgen-worker:
    build:
      context: .
      dockerfile: Dockerfile.docgen  # Includes Node.js, LibreOffice, Poppler
    command: >
      celery -A src.celery_app worker
      --queues=rag.doc_gen --concurrency=2 --pool=prefork
      --hostname=docgen@%h
    deploy:
      replicas: 2
      resources:
        limits: { cpus: "4", memory: 8G }
    env_file: .env
    depends_on: [postgres, redis]

  ingestion-worker:
    build:
      context: .
      dockerfile: Dockerfile.docgen  # Same deps as docgen (OCR, parsers)
    command: >
      celery -A src.celery_app worker
      --queues=rag.ingestion --concurrency=4 --pool=prefork
      --hostname=ingestion@%h
    deploy:
      replicas: 1
      resources:
        limits: { cpus: "4", memory: 8G }
    env_file: .env
    depends_on: [postgres, neo4j, redis]

  # === Celery Beat (Scheduled Tasks) ===
  celery-beat:
    build: .
    command: celery -A src.celery_app beat --loglevel=info
    env_file: .env
    depends_on: [redis]

  # === Data Stores ===
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: betterrag
      POSTGRES_USER: ${PGVECTOR_USER}
      POSTGRES_PASSWORD: ${PGVECTOR_PASSWORD}
    command: >
      postgres
      -c max_connections=300
      -c shared_buffers=2GB
      -c effective_cache_size=6GB
      -c work_mem=64MB
      -c maintenance_work_mem=512MB
    deploy:
      resources:
        limits: { cpus: "4", memory: 8G }
    volumes:
      - pgdata:/var/lib/postgresql/data

  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: ${NEO4J_USER}/${NEO4J_PASSWORD}
      NEO4J_server_memory_heap_initial__size: 2G
      NEO4J_server_memory_heap_max__size: 4G
      NEO4J_server_memory_pagecache_size: 2G
    deploy:
      resources:
        limits: { cpus: "4", memory: 8G }
    volumes:
      - neo4jdata:/data

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --maxmemory 4gb
      --maxmemory-policy allkeys-lru
      --maxclients 10000
      --timeout 1800
      --save ""
      --appendonly no
    deploy:
      resources:
        limits: { cpus: "2", memory: 4G }

  # === Load Balancer ===
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./certs:/etc/nginx/certs
    depends_on: [open-webui, api]

volumes:
  pgdata:
  neo4jdata:
```

---

### 11.12 Celery Beat — Scheduled Tasks

```python
# config/celery_config.py — Add to existing config

from celery.schedules import crontab

beat_schedule = {
    # Re-expand group → user permissions every 4 hours
    "refresh-permissions": {
        "task": "src.celery_app.run_permission_refresh",
        "schedule": crontab(minute=0, hour="*/4"),
        "options": {"queue": "rag.ingestion"},
    },
    # SharePoint delta sync every 15 minutes
    "sharepoint-delta-sync": {
        "task": "src.celery_app.run_delta_sync",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "rag.ingestion"},
    },
    # Cleanup expired Redis Streams (safety net)
    "cleanup-expired-streams": {
        "task": "src.celery_app.cleanup_streams",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "rag.query"},
    },
    # LangGraph checkpoint cleanup (older than 1 hour)
    "cleanup-checkpoints": {
        "task": "src.celery_app.cleanup_checkpoints",
        "schedule": crontab(minute=0, hour="*/1"),
        "options": {"queue": "rag.query"},
    },
}
```

---

### 11.13 Capacity Planning Summary

| Component | Replicas | Concurrency | Total Capacity | Resource per Instance |
|-----------|----------|-------------|----------------|----------------------|
| Open WebUI | 3 | THREAD_POOL_SIZE=500 | 800+ WebSocket connections | 2 CPU, 4GB RAM |
| FastAPI API | 3 | 4 uvicorn workers each | 1000+ SSE connections | 2 CPU, 2GB RAM |
| Query Worker | 3 | 10 gevent greenlets each | **30 concurrent queries** | 4 CPU, 8GB RAM |
| Doc-Gen Worker | 2 | 2 prefork processes each | **4 concurrent generations** | 4 CPU, 8GB RAM |
| Ingestion Worker | 1 | 4 prefork processes | 4 concurrent ingestions | 4 CPU, 8GB RAM |
| PostgreSQL | 1 | max_connections=300 | Shared across all | 4 CPU, 8GB RAM |
| Neo4j | 1 | 50 bolt connections | Shared across all | 4 CPU, 8GB RAM |
| Redis | 1 | 10,000 clients | Shared across all | 2 CPU, 4GB RAM |

**Total infrastructure:** ~36 CPU cores, ~58GB RAM

**At 800 users, 15% peak concurrency (120 simultaneous):**
- 30 query worker slots → handles 120 requests/8s avg = 15 req/s throughput
- If P95 latency target is 15s, 30 slots handles ~120 concurrent with headroom
- Doc-gen: 4 slots × 30s avg = handles ~8 doc-gen/min (5% of 120 = 6 concurrent, slight queuing acceptable)

**Horizontal scaling path:** Add more `query-worker` replicas as user count grows. Each replica adds 10 concurrent query slots. Going from 800 → 2000 users: add 3 more query workers (6 total, 60 concurrent queries).

---

### 11.14 Key Design Decisions (Scaling)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Execution model | Celery workers (not in-process) | Decouples SSE connection handling from LangGraph execution; allows independent scaling |
| SSE bridge | Redis Streams (not Pub/Sub) | Persistent, no lost events, backpressure, auto-cleanup |
| Query worker pool | gevent (greenlets) | LangGraph is I/O-bound (LLM API calls); greenlets handle 10 concurrent waits per worker efficiently |
| Doc-gen worker pool | prefork (processes) | CPU-heavy subprocess calls (LibreOffice, Node.js); processes avoid GIL contention |
| LangGraph checkpointing | AsyncRedisSaver | 12x faster than PostgreSQL checkpoints; enables crash recovery for long-running tasks |
| Redis topology | Single instance, multiple DBs | Sufficient for 800 users (~1.2GB peak); simpler ops than Redis Cluster |
| Open WebUI scaling | 3 replicas + PostgreSQL + Redis | Official scaling guide; WebSocket coordination via Redis |
| Separate Dockerfiles | `Dockerfile` (API/query) vs `Dockerfile.docgen` (doc-gen/ingestion) | Doc-gen needs Node.js + LibreOffice + Poppler; query workers don't — smaller image, faster deploys |
| Cache strategy | Redis db=3 with key-specific TTLs | HyDE cache (1hr), RBAC cache (5min), query dedup (2min) — each TTL matches data freshness needs |

---

### 11.15 Configuration Additions

```env
# .env additions for scaling

# Celery
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Redis Streams (SSE bridge)
REDIS_STREAMS_URL=redis://redis:6379/2

# Cache
REDIS_CACHE_URL=redis://redis:6379/3
CACHE_HYDE_TTL=3600
CACHE_RBAC_TTL=300
CACHE_QUERY_TTL=120

# LangGraph Checkpoints
LANGGRAPH_CHECKPOINT_REDIS_URL=redis://redis:6379/4

# PostgreSQL tuning
PGVECTOR_POOL_SIZE=20
PGVECTOR_POOL_MAX_OVERFLOW=10

# Worker scaling
QUERY_WORKER_CONCURRENCY=10
DOCGEN_WORKER_CONCURRENCY=2
INGESTION_WORKER_CONCURRENCY=4

# LLM rate limits
LLM_RATE_LIMIT_RPM=500
LLM_CONCURRENT_REQUESTS=50
```

---

## Section 12: AKS (Azure Kubernetes Service) Deployment

This section replaces the Docker Compose production deployment (Section 11.11) with a production-grade AKS cluster deployment. The Docker Compose files remain useful for local development.

### 12.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Azure Subscription                                 │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    AKS Cluster (better-rag-aks)                      │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────┐                       │  │
│  │  │     System Node Pool (system)            │                       │  │
│  │  │     Standard_D4s_v5 × 3 nodes            │                       │  │
│  │  │     • CoreDNS, kube-proxy, metrics-server│                       │  │
│  │  │     • KEDA operator                      │                       │  │
│  │  │     • cert-manager                       │                       │  │
│  │  │     • nginx-ingress controller           │                       │  │
│  │  │     • secrets-store-csi-driver           │                       │  │
│  │  └──────────────────────────────────────────┘                       │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────┐                       │  │
│  │  │     App Node Pool (apppool)              │                       │  │
│  │  │     Standard_D8s_v5 × 3-6 nodes (KEDA)  │                       │  │
│  │  │     • open-webui (3 replicas)            │                       │  │
│  │  │     • better-rag-api (3 replicas)        │                       │  │
│  │  │     • query-worker (3-8, KEDA-scaled)    │                       │  │
│  │  │     • celery-beat (1 replica)            │                       │  │
│  │  └──────────────────────────────────────────┘                       │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────┐                       │  │
│  │  │     Heavy Node Pool (heavypool)          │                       │  │
│  │  │     Standard_D8s_v5 × 2-4 nodes (KEDA)  │                       │  │
│  │  │     • docgen-worker (2-6, KEDA-scaled)   │                       │  │
│  │  │     • ingestion-worker (1-3, KEDA-scaled)│                       │  │
│  │  │     Taint: workload=heavy:NoSchedule     │                       │  │
│  │  └──────────────────────────────────────────┘                       │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────┐                       │  │
│  │  │     Data Node Pool (datapool)            │                       │  │
│  │  │     Standard_E8s_v5 × 2 nodes            │                       │  │
│  │  │     (memory-optimized for Neo4j)         │                       │  │
│  │  │     • neo4j (1 standalone)               │                       │  │
│  │  │     Taint: workload=data:NoSchedule      │                       │  │
│  │  └──────────────────────────────────────────┘                       │  │
│  │                                                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    Azure Managed Services                            │  │
│  │                                                                      │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐    │  │
│  │  │ Azure DB for │ │ Azure Cache  │ │ Azure Container Registry │    │  │
│  │  │ PostgreSQL   │ │ for Redis    │ │ (ACR)                    │    │  │
│  │  │ Flexible     │ │ Premium P1   │ │ better-rag images        │    │  │
│  │  │ + pgvector   │ │ 6GB          │ │                          │    │  │
│  │  └──────────────┘ └──────────────┘ └──────────────────────────┘    │  │
│  │                                                                      │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐    │  │
│  │  │ Azure Key    │ │ Azure Monitor│ │ Azure Blob Storage       │    │  │
│  │  │ Vault        │ │ + Managed    │ │ (staging + generated     │    │  │
│  │  │ (secrets)    │ │ Prometheus + │ │  files)                  │    │  │
│  │  │              │ │ Grafana      │ │                          │    │  │
│  │  └──────────────┘ └──────────────┘ └──────────────────────────┘    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### 12.2 Azure Managed Services vs Self-Hosted

| Component | Choice | Service | Rationale |
|-----------|--------|---------|-----------|
| PostgreSQL + pgvector | **Azure Managed** | Azure Database for PostgreSQL Flexible Server | Auto-backups, HA, patching, pgvector extension supported natively (`CREATE EXTENSION vector`). General Purpose D4s_v3 (4 vCPU, 16GB). No ops burden. |
| Redis | **Azure Managed** | Azure Cache for Redis Premium P1 | 6GB, clustering-ready, persistence, geo-replication option. Supports multiple databases (db0-4). No container overhead. |
| Neo4j | **Self-hosted (Helm)** | Neo4j Helm chart in-cluster | No Azure managed Neo4j. Official Helm chart (`neo4j/neo4j`) with PVC on Azure Disk. |
| Container Registry | **Azure Managed** | Azure Container Registry (ACR) | Native AKS integration (no image pull secrets needed). Basic tier sufficient. |
| Secrets | **Azure Managed** | Azure Key Vault + CSI Driver | Workload Identity federation — pods access secrets without stored credentials. |
| Monitoring | **Azure Managed** | Azure Monitor + Managed Prometheus + Managed Grafana | Container Insights for logs, Managed Prometheus for metrics, Grafana dashboards. Zero cluster-side ops. |
| Blob Storage | **Azure Managed** | Azure Blob Storage | Already used for document staging. Also serves generated files. |
| TLS Certificates | **In-cluster** | cert-manager + Let's Encrypt | Automatic cert provisioning and renewal. DNS-01 challenge via Azure DNS + Workload Identity. |
| Ingress | **In-cluster** | NGINX Ingress Controller | Better SSE/WebSocket support than AGIC. Annotations for long timeouts, proxy buffering off. |

---

### 12.3 AKS Cluster Provisioning (Terraform)

```hcl
# infra/terraform/main.tf

resource "azurerm_kubernetes_cluster" "aks" {
  name                = "better-rag-aks"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "better-rag"
  kubernetes_version  = "1.30"

  oidc_issuer_enabled       = true   # Required for Workload Identity
  workload_identity_enabled = true   # Pod-level managed identity

  default_node_pool {
    name                = "system"
    vm_size             = "Standard_D4s_v5"    # 4 vCPU, 16GB RAM
    node_count          = 3
    os_disk_type        = "Ephemeral"
    only_critical_addons_enabled = true        # Only system pods
    temporary_name_for_rotation  = "systmp"
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"       # Azure CNI for better perf
    network_policy    = "calico"      # For NetworkPolicy support
    load_balancer_sku = "standard"
    service_cidr      = "10.0.0.0/16"
    dns_service_ip    = "10.0.0.10"
  }

  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "5m"
  }

  monitor_metrics {}  # Enables Azure Monitor metrics

  tags = {
    project     = "better-rag"
    environment = "production"
  }
}

# --- User Node Pools ---

resource "azurerm_kubernetes_cluster_node_pool" "apppool" {
  name                  = "apppool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"    # 8 vCPU, 32GB RAM
  os_disk_type          = "Ephemeral"
  min_count             = 3
  max_count             = 6
  auto_scaling_enabled  = true                  # Cluster autoscaler

  node_labels = {
    "workload" = "app"
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "heavypool" {
  name                  = "heavypool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D8s_v5"    # 8 vCPU, 32GB RAM
  os_disk_type          = "Ephemeral"
  min_count             = 2
  max_count             = 4
  auto_scaling_enabled  = true

  node_labels = {
    "workload" = "heavy"
  }
  node_taints = ["workload=heavy:NoSchedule"]
}

resource "azurerm_kubernetes_cluster_node_pool" "datapool" {
  name                  = "datapool"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_E8s_v5"    # 8 vCPU, 64GB RAM (memory-optimized)
  os_disk_type          = "Managed"             # Persistent for Neo4j
  node_count            = 2
  auto_scaling_enabled  = false                 # Fixed for stateful workload

  node_labels = {
    "workload" = "data"
  }
  node_taints = ["workload=data:NoSchedule"]
}

# --- Azure Database for PostgreSQL Flexible Server ---

resource "azurerm_postgresql_flexible_server" "pg" {
  name                          = "better-rag-pg"
  resource_group_name           = azurerm_resource_group.rg.name
  location                      = azurerm_resource_group.rg.location
  version                       = "16"
  sku_name                      = "GP_Standard_D4s_v3"  # 4 vCPU, 16GB
  storage_mb                    = 131072                  # 128GB
  zone                          = "1"
  public_network_access_enabled = false                   # VNet only

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  high_availability {
    mode = "ZoneRedundant"
  }
}

# Enable pgvector extension
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  server_id = azurerm_postgresql_flexible_server.pg.id
  name      = "azure.extensions"
  value     = "vector,pg_trgm,btree_gin"
}

resource "azurerm_postgresql_flexible_server_configuration" "max_connections" {
  server_id = azurerm_postgresql_flexible_server.pg.id
  name      = "max_connections"
  value     = "300"
}

resource "azurerm_postgresql_flexible_server_configuration" "shared_buffers" {
  server_id = azurerm_postgresql_flexible_server.pg.id
  name      = "shared_buffers"
  value     = "1048576"   # ~4GB in 8KB pages
}

# --- Azure Cache for Redis ---

resource "azurerm_redis_cache" "redis" {
  name                = "better-rag-redis"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  capacity            = 1                              # P1 = 6GB
  family              = "P"                            # Premium
  sku_name            = "Premium"

  minimum_tls_version = "1.2"
  redis_configuration {
    maxmemory_policy = "allkeys-lru"
  }

  # Private endpoint for VNet access
  public_network_access_enabled = false
}

# --- Azure Key Vault ---

resource "azurerm_key_vault" "kv" {
  name                       = "better-rag-kv"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true    # RBAC mode (not Access Policies)
  purge_protection_enabled   = true
}

# --- Azure Container Registry ---

resource "azurerm_container_registry" "acr" {
  name                = "betterragacr"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Standard"
  admin_enabled       = false
}

# ACR → AKS pull permission
resource "azurerm_role_assignment" "acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.acr.id
  skip_service_principal_aad_check = true
}

# --- Azure Monitor ---

resource "azurerm_monitor_workspace" "prometheus" {
  name                = "better-rag-prometheus"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
}

resource "azurerm_dashboard_grafana" "grafana" {
  name                = "better-rag-grafana"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  grafana_major_version = 10

  azure_monitor_workspace_integrations {
    resource_id = azurerm_monitor_workspace.prometheus.id
  }
}

# --- Private Endpoints (VNet integration) ---

resource "azurerm_private_endpoint" "pg_pe" {
  name                = "better-rag-pg-pe"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  subnet_id           = azurerm_subnet.endpoints.id

  private_service_connection {
    name                           = "pg-connection"
    private_connection_resource_id = azurerm_postgresql_flexible_server.pg.id
    subresource_names              = ["postgresqlServer"]
    is_manual_connection           = false
  }
}

resource "azurerm_private_endpoint" "redis_pe" {
  name                = "better-rag-redis-pe"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  subnet_id           = azurerm_subnet.endpoints.id

  private_service_connection {
    name                           = "redis-connection"
    private_connection_resource_id = azurerm_redis_cache.redis.id
    subresource_names              = ["redisCache"]
    is_manual_connection           = false
  }
}
```

---

### 12.4 Azure Workload Identity + Key Vault CSI Driver

Pods authenticate to Azure services (Key Vault, Blob Storage, Azure OpenAI) using **Workload Identity** — federated credentials tied to Kubernetes ServiceAccounts. No secrets stored in the cluster.

```yaml
# k8s/base/workload-identity.yaml

# 1. ServiceAccount with Workload Identity annotation
apiVersion: v1
kind: ServiceAccount
metadata:
  name: better-rag-sa
  namespace: better-rag
  annotations:
    azure.workload.identity/client-id: "${UAMI_CLIENT_ID}"
  labels:
    azure.workload.identity/use: "true"
---
# 2. SecretProviderClass — mounts Key Vault secrets as files/env vars
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: better-rag-secrets
  namespace: better-rag
spec:
  provider: azure
  parameters:
    usePodIdentity: "false"
    useVMManagedIdentity: "false"
    clientID: "${UAMI_CLIENT_ID}"
    keyvaultName: "better-rag-kv"
    tenantId: "${TENANT_ID}"
    objects: |
      array:
        - |
          objectName: graph-client-secret
          objectType: secret
        - |
          objectName: oauth-client-secret
          objectType: secret
        - |
          objectName: better-rag-api-key
          objectType: secret
        - |
          objectName: anthropic-api-key
          objectType: secret
        - |
          objectName: azure-openai-api-key
          objectType: secret
        - |
          objectName: neo4j-password
          objectType: secret
        - |
          objectName: ocr-azure-key
          objectType: secret
  secretObjects:
    - secretName: better-rag-secrets
      type: Opaque
      data:
        - objectName: graph-client-secret
          key: GRAPH_CLIENT_SECRET
        - objectName: oauth-client-secret
          key: OAUTH_CLIENT_SECRET
        - objectName: better-rag-api-key
          key: BETTER_RAG_API_KEY
        - objectName: anthropic-api-key
          key: ANTHROPIC_API_KEY
        - objectName: azure-openai-api-key
          key: AZURE_OPENAI_API_KEY
        - objectName: neo4j-password
          key: NEO4J_PASSWORD
        - objectName: ocr-azure-key
          key: OCR_AZURE_KEY
```

**Terraform for Workload Identity federation:**
```hcl
# infra/terraform/workload_identity.tf

resource "azurerm_user_assigned_identity" "better_rag" {
  name                = "better-rag-identity"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
}

resource "azurerm_federated_identity_credential" "aks_federation" {
  name                = "aks-better-rag-federation"
  resource_group_name = azurerm_resource_group.rg.name
  parent_id           = azurerm_user_assigned_identity.better_rag.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.aks.oidc_issuer_url
  subject             = "system:serviceaccount:better-rag:better-rag-sa"
}

# Grant Key Vault access
resource "azurerm_role_assignment" "kv_secrets" {
  principal_id         = azurerm_user_assigned_identity.better_rag.principal_id
  role_definition_name = "Key Vault Secrets User"
  scope                = azurerm_key_vault.kv.id
}

# Grant Blob Storage access
resource "azurerm_role_assignment" "blob_contributor" {
  principal_id         = azurerm_user_assigned_identity.better_rag.principal_id
  role_definition_name = "Storage Blob Data Contributor"
  scope                = azurerm_storage_account.staging.id
}
```

---

### 12.5 Kubernetes Manifests Structure

```
k8s/
├── base/                              # Kustomize base
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   ├── workload-identity.yaml         # ServiceAccount + SecretProviderClass
│   ├── configmap.yaml                 # Non-secret environment variables
│   │
│   ├── api/
│   │   ├── deployment.yaml            # better-rag-api (FastAPI SSE gateway)
│   │   ├── service.yaml
│   │   └── hpa.yaml                   # HPA for API servers
│   │
│   ├── workers/
│   │   ├── query-worker.yaml          # Deployment + KEDA ScaledObject
│   │   ├── docgen-worker.yaml         # Deployment + KEDA ScaledObject
│   │   ├── ingestion-worker.yaml      # Deployment + KEDA ScaledObject
│   │   └── celery-beat.yaml           # Single-replica Deployment
│   │
│   ├── open-webui/
│   │   ├── values.yaml                # Helm values override
│   │   └── pipe-function-cm.yaml      # ConfigMap with Pipe Function code
│   │
│   ├── neo4j/
│   │   └── values.yaml                # Helm values override
│   │
│   ├── ingress/
│   │   ├── ingress.yaml               # NGINX Ingress rules
│   │   └── cluster-issuer.yaml        # cert-manager ClusterIssuer
│   │
│   └── network-policies/
│       ├── default-deny.yaml
│       ├── allow-api-to-redis.yaml
│       ├── allow-workers-to-stores.yaml
│       ├── allow-owui-to-api.yaml
│       └── allow-ingress.yaml
│
├── overlays/
│   ├── dev/
│   │   ├── kustomization.yaml         # Lower replicas, smaller resources
│   │   └── patches/
│   └── prod/
│       ├── kustomization.yaml         # Full replicas, production resources
│       └── patches/
│
└── helm-releases/                     # Helm release configs
    ├── nginx-ingress.yaml
    ├── cert-manager.yaml
    ├── keda.yaml
    ├── open-webui.yaml
    └── neo4j.yaml
```

---

### 12.6 Core Kubernetes Deployments

#### API Server (FastAPI SSE Gateway)

```yaml
# k8s/base/api/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: better-rag-api
  namespace: better-rag
spec:
  replicas: 3
  selector:
    matchLabels:
      app: better-rag-api
  template:
    metadata:
      labels:
        app: better-rag-api
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: better-rag-sa
      nodeSelector:
        workload: app
      containers:
        - name: api
          image: betterragacr.azurecr.io/better-rag-api:latest
          command: ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000",
                    "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: better-rag-config
            - secretRef:
                name: better-rag-secrets
          resources:
            requests:
              cpu: "1"
              memory: "1Gi"
            limits:
              cpu: "2"
              memory: "2Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 20
          volumeMounts:
            - name: secrets-store
              mountPath: /mnt/secrets-store
              readOnly: true
      volumes:
        - name: secrets-store
          csi:
            driver: secrets-store.csi.k8s.io
            readOnly: true
            volumeAttributes:
              secretProviderClass: better-rag-secrets
---
# k8s/base/api/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: better-rag-api
  namespace: better-rag
spec:
  selector:
    app: better-rag-api
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
---
# k8s/base/api/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: better-rag-api
  namespace: better-rag
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: better-rag-api
  minReplicas: 3
  maxReplicas: 8
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 25
          periodSeconds: 120
```

#### Query Worker (KEDA-Scaled)

```yaml
# k8s/base/workers/query-worker.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: query-worker
  namespace: better-rag
spec:
  replicas: 3   # KEDA manages this
  selector:
    matchLabels:
      app: query-worker
  template:
    metadata:
      labels:
        app: query-worker
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: better-rag-sa
      nodeSelector:
        workload: app
      containers:
        - name: worker
          image: betterragacr.azurecr.io/better-rag-api:latest
          command: ["celery", "-A", "src.celery_app", "worker",
                    "--queues=rag.query",
                    "--concurrency=10",
                    "--pool=gevent",
                    "--hostname=query@%h"]
          envFrom:
            - configMapRef:
                name: better-rag-config
            - secretRef:
                name: better-rag-secrets
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "4"
              memory: "8Gi"
          volumeMounts:
            - name: secrets-store
              mountPath: /mnt/secrets-store
              readOnly: true
      volumes:
        - name: secrets-store
          csi:
            driver: secrets-store.csi.k8s.io
            readOnly: true
            volumeAttributes:
              secretProviderClass: better-rag-secrets
---
# KEDA ScaledObject — scales query workers based on Redis queue length
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: query-worker-scaler
  namespace: better-rag
spec:
  scaleTargetRef:
    name: query-worker
  pollingInterval: 15              # Check queue every 15s
  cooldownPeriod: 120              # Wait 2min before scaling down
  minReplicaCount: 3               # Always keep 3 running
  maxReplicaCount: 8               # Max 8 pods = 80 concurrent queries
  triggers:
    - type: redis
      metadata:
        address: "${REDIS_HOST}:${REDIS_PORT}"
        databaseIndex: "1"
        listName: "rag.query"      # Celery queue name in Redis
        listLength: "5"            # Scale up when >5 tasks queued
        enableTLS: "true"
      authenticationRef:
        name: redis-auth
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 30
          policies:
            - type: Pods
              value: 2
              periodSeconds: 30
        scaleDown:
          stabilizationWindowSeconds: 300
          policies:
            - type: Percent
              value: 25
              periodSeconds: 120
---
# KEDA TriggerAuthentication for Redis
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: redis-auth
  namespace: better-rag
spec:
  secretTargetRef:
    - parameter: password
      name: better-rag-secrets
      key: REDIS_PASSWORD
```

#### Doc-Gen Worker (KEDA-Scaled, Heavy Pool)

```yaml
# k8s/base/workers/docgen-worker.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: docgen-worker
  namespace: better-rag
spec:
  replicas: 2
  selector:
    matchLabels:
      app: docgen-worker
  template:
    metadata:
      labels:
        app: docgen-worker
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: better-rag-sa
      nodeSelector:
        workload: heavy
      tolerations:
        - key: "workload"
          operator: "Equal"
          value: "heavy"
          effect: "NoSchedule"
      containers:
        - name: worker
          image: betterragacr.azurecr.io/better-rag-docgen:latest  # Larger image with Node.js + LibreOffice
          command: ["celery", "-A", "src.celery_app", "worker",
                    "--queues=rag.doc_gen",
                    "--concurrency=2",
                    "--pool=prefork",
                    "--hostname=docgen@%h"]
          envFrom:
            - configMapRef:
                name: better-rag-config
            - secretRef:
                name: better-rag-secrets
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "4"
              memory: "8Gi"
          volumeMounts:
            - name: secrets-store
              mountPath: /mnt/secrets-store
              readOnly: true
            - name: tmp-docgen
              mountPath: /tmp       # LibreOffice + Node.js scratch space
      volumes:
        - name: secrets-store
          csi:
            driver: secrets-store.csi.k8s.io
            readOnly: true
            volumeAttributes:
              secretProviderClass: better-rag-secrets
        - name: tmp-docgen
          emptyDir:
            sizeLimit: 2Gi
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: docgen-worker-scaler
  namespace: better-rag
spec:
  scaleTargetRef:
    name: docgen-worker
  pollingInterval: 15
  cooldownPeriod: 300              # Longer cooldown (doc-gen is bursty)
  minReplicaCount: 2
  maxReplicaCount: 6
  triggers:
    - type: redis
      metadata:
        address: "${REDIS_HOST}:${REDIS_PORT}"
        databaseIndex: "1"
        listName: "rag.doc_gen"
        listLength: "2"            # Scale up when >2 tasks queued
        enableTLS: "true"
      authenticationRef:
        name: redis-auth
```

#### Ingestion Worker (KEDA-Scaled, Heavy Pool)

```yaml
# k8s/base/workers/ingestion-worker.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ingestion-worker
  namespace: better-rag
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ingestion-worker
  template:
    metadata:
      labels:
        app: ingestion-worker
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: better-rag-sa
      nodeSelector:
        workload: heavy
      tolerations:
        - key: "workload"
          operator: "Equal"
          value: "heavy"
          effect: "NoSchedule"
      containers:
        - name: worker
          image: betterragacr.azurecr.io/better-rag-docgen:latest
          command: ["celery", "-A", "src.celery_app", "worker",
                    "--queues=rag.ingestion",
                    "--concurrency=4",
                    "--pool=prefork",
                    "--hostname=ingestion@%h"]
          envFrom:
            - configMapRef:
                name: better-rag-config
            - secretRef:
                name: better-rag-secrets
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "4"
              memory: "8Gi"
          volumeMounts:
            - name: secrets-store
              mountPath: /mnt/secrets-store
              readOnly: true
      volumes:
        - name: secrets-store
          csi:
            driver: secrets-store.csi.k8s.io
            readOnly: true
            volumeAttributes:
              secretProviderClass: better-rag-secrets
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: ingestion-worker-scaler
  namespace: better-rag
spec:
  scaleTargetRef:
    name: ingestion-worker
  pollingInterval: 30
  cooldownPeriod: 600
  minReplicaCount: 1
  maxReplicaCount: 3
  triggers:
    - type: redis
      metadata:
        address: "${REDIS_HOST}:${REDIS_PORT}"
        databaseIndex: "1"
        listName: "rag.ingestion"
        listLength: "10"           # Scale up during bulk ingestion
        enableTLS: "true"
      authenticationRef:
        name: redis-auth
```

#### Celery Beat (Single Replica)

```yaml
# k8s/base/workers/celery-beat.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-beat
  namespace: better-rag
spec:
  replicas: 1                      # Must be exactly 1
  strategy:
    type: Recreate                 # No duplicate schedulers
  selector:
    matchLabels:
      app: celery-beat
  template:
    metadata:
      labels:
        app: celery-beat
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: better-rag-sa
      nodeSelector:
        workload: app
      containers:
        - name: beat
          image: betterragacr.azurecr.io/better-rag-api:latest
          command: ["celery", "-A", "src.celery_app", "beat", "--loglevel=info"]
          envFrom:
            - configMapRef:
                name: better-rag-config
            - secretRef:
                name: better-rag-secrets
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
```

---

### 12.7 Helm Chart Deployments

#### NGINX Ingress Controller

```yaml
# k8s/helm-releases/nginx-ingress.yaml
# helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
# helm install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace -f k8s/helm-releases/nginx-ingress.yaml

controller:
  replicaCount: 2
  nodeSelector:
    kubernetes.io/os: linux
  service:
    annotations:
      service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path: /healthz
  config:
    # SSE/WebSocket support
    proxy-read-timeout: "3600"       # 1 hour for long SSE connections
    proxy-send-timeout: "3600"
    proxy-buffering: "off"           # Critical for SSE streaming
    use-forwarded-headers: "true"
    enable-real-ip: "true"
    # Performance
    worker-processes: "auto"
    keep-alive: "75"
  resources:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: "1"
      memory: 1Gi
```

#### cert-manager + Let's Encrypt

```yaml
# k8s/helm-releases/cert-manager.yaml
# helm repo add jetstack https://charts.jetstack.io
# helm install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true -f k8s/helm-releases/cert-manager.yaml

replicaCount: 2
podLabels:
  azure.workload.identity/use: "true"
serviceAccount:
  annotations:
    azure.workload.identity/client-id: "${CERT_MANAGER_UAMI_CLIENT_ID}"
```

```yaml
# k8s/base/ingress/cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: devops@contoso.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - dns01:
          azureDNS:
            subscriptionID: "${SUBSCRIPTION_ID}"
            resourceGroupName: "${DNS_RG}"
            hostedZoneName: "betterrag.contoso.com"
            environment: AzurePublicCloud
            managedIdentity:
              clientID: "${CERT_MANAGER_UAMI_CLIENT_ID}"
```

#### KEDA

```yaml
# k8s/helm-releases/keda.yaml
# helm repo add kedacore https://kedacore.github.io/charts
# helm install keda kedacore/keda -n keda --create-namespace -f k8s/helm-releases/keda.yaml

resources:
  operator:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
  metricsServer:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi
```

#### Open WebUI

```yaml
# k8s/helm-releases/open-webui.yaml
# helm repo add open-webui https://helm.openwebui.com
# helm install open-webui open-webui/open-webui -n better-rag -f k8s/helm-releases/open-webui.yaml

replicaCount: 3

nodeSelector:
  workload: app

# Disable bundled Ollama (we use our own backend)
ollama:
  enabled: false

# Disable bundled pipelines
pipelines:
  enabled: false

# Disable bundled Tika
tika:
  enabled: false

# Environment variables
extraEnvVars:
  - name: DATABASE_URL
    value: "postgresql://${PGVECTOR_USER}:${PGVECTOR_PASSWORD}@${PG_HOST}:5432/openwebui"
  - name: DATABASE_POOL_SIZE
    value: "15"
  - name: REDIS_URL
    value: "rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/0"   # TLS on port 6380
  - name: WEBSOCKET_MANAGER
    value: "redis"
  - name: ENABLE_WEBSOCKET_SUPPORT
    value: "true"
  - name: ENABLE_OAUTH_SIGNUP
    value: "true"
  - name: OAUTH_PROVIDER_NAME
    value: "microsoft"
  - name: OPENID_PROVIDER_URL
    value: "https://login.microsoftonline.com/${TENANT_ID}/v2.0/.well-known/openid-configuration"
  - name: OAUTH_SCOPES
    value: "openid profile email User.Read"
  - name: ENABLE_OAUTH_GROUP_MANAGEMENT
    value: "true"
  - name: OAUTH_MERGE_ACCOUNTS_BY_EMAIL
    value: "true"
  - name: DEFAULT_MODELS
    value: "better-rag-agent"
  - name: UVICORN_WORKERS
    value: "1"
  - name: THREAD_POOL_SIZE
    value: "500"
  - name: VECTOR_DB
    value: "pgvector"
  - name: STORAGE_PROVIDER
    value: "s3"
  - name: S3_ENDPOINT_URL
    value: "https://${STORAGE_ACCOUNT}.blob.core.windows.net"
  - name: S3_BUCKET_NAME
    value: "openwebui-files"

envFrom:
  - secretRef:
      name: open-webui-secrets     # OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, WEBUI_SECRET_KEY

persistence:
  enabled: true
  storageClass: managed-csi
  size: 10Gi

resources:
  requests:
    cpu: "1"
    memory: 2Gi
  limits:
    cpu: "2"
    memory: 4Gi

service:
  type: ClusterIP
  port: 8080
```

#### Neo4j

```yaml
# k8s/helm-releases/neo4j.yaml
# helm repo add neo4j https://helm.neo4j.com/neo4j
# helm install neo4j neo4j/neo4j -n better-rag -f k8s/helm-releases/neo4j.yaml

neo4j:
  name: "better-rag-neo4j"
  edition: "community"
  acceptLicenseAgreement: "yes"
  password: ""   # Sourced from Secret

  resources:
    requests:
      cpu: "2"
      memory: "4Gi"
    limits:
      cpu: "4"
      memory: "8Gi"

nodeSelector:
  workload: data

tolerations:
  - key: "workload"
    operator: "Equal"
    value: "data"
    effect: "NoSchedule"

volumes:
  data:
    mode: dynamic
    dynamic:
      storageClassName: managed-csi-premium    # Azure Premium SSD
      requests:
        storage: 64Gi

config:
  server.memory.heap.initial_size: "2g"
  server.memory.heap.max_size: "4g"
  server.memory.pagecache.size: "2g"
  server.bolt.advertised_address: ":7687"

services:
  neo4j:
    type: ClusterIP
```

---

### 12.8 Ingress Configuration

```yaml
# k8s/base/ingress/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: better-rag-ingress
  namespace: better-rag
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"        # SSE streaming
    nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"       # Large file uploads
    nginx.ingress.kubernetes.io/websocket-services: "open-webui"
    nginx.ingress.kubernetes.io/configuration-snippet: |
      proxy_set_header Connection '';
      proxy_http_version 1.1;
      chunked_transfer_encoding off;
      proxy_cache off;
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - rag.contoso.com
      secretName: better-rag-tls
  rules:
    - host: rag.contoso.com
      http:
        paths:
          # Open WebUI frontend (primary)
          - path: /
            pathType: Prefix
            backend:
              service:
                name: open-webui
                port:
                  number: 8080
          # better-rag API (internal, but exposed for Pipe Function health checks)
          - path: /api/v1/files/generated
            pathType: Prefix
            backend:
              service:
                name: better-rag-api
                port:
                  number: 8000
```

**Why NGINX over AGIC?**
- AGIC has higher latency for config updates (relies on ARM API vs in-cluster reconciliation)
- NGINX has better SSE/WebSocket support with fine-grained annotations (`proxy-buffering: off`, `chunked_transfer_encoding off`)
- NGINX runs in-cluster = faster failover
- AGIC advantage (WAF, lower resource usage) is less relevant — our traffic is internal enterprise users

---

### 12.9 Network Policies

```yaml
# k8s/base/network-policies/default-deny.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: better-rag
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
# k8s/base/network-policies/allow-api-to-redis.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-api-egress
  namespace: better-rag
spec:
  podSelector:
    matchLabels:
      app: better-rag-api
  policyTypes:
    - Egress
  egress:
    # Redis (Azure Cache — Private Endpoint)
    - to:
        - ipBlock:
            cidr: "${REDIS_PRIVATE_IP}/32"
      ports:
        - port: 6380
          protocol: TCP
    # PostgreSQL (Azure Flexible Server — Private Endpoint)
    - to:
        - ipBlock:
            cidr: "${PG_PRIVATE_IP}/32"
      ports:
        - port: 5432
          protocol: TCP
    # DNS
    - to: []
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
---
# k8s/base/network-policies/allow-owui-to-api.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-owui-to-api
  namespace: better-rag
spec:
  podSelector:
    matchLabels:
      app: better-rag-api
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: open-webui
      ports:
        - port: 8000
          protocol: TCP
---
# k8s/base/network-policies/allow-workers-to-stores.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-workers-egress
  namespace: better-rag
spec:
  podSelector:
    matchExpressions:
      - key: app
        operator: In
        values: [query-worker, docgen-worker, ingestion-worker]
  policyTypes:
    - Egress
  egress:
    # Redis
    - to:
        - ipBlock:
            cidr: "${REDIS_PRIVATE_IP}/32"
      ports:
        - port: 6380
    # PostgreSQL
    - to:
        - ipBlock:
            cidr: "${PG_PRIVATE_IP}/32"
      ports:
        - port: 5432
    # Neo4j (in-cluster)
    - to:
        - podSelector:
            matchLabels:
              app: neo4j
      ports:
        - port: 7687
    # Azure OpenAI / Anthropic API / Azure Blob / Azure DI (HTTPS outbound)
    - to: []
      ports:
        - port: 443
          protocol: TCP
    # DNS
    - to: []
      ports:
        - port: 53
          protocol: UDP
---
# k8s/base/network-policies/allow-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-to-owui
  namespace: better-rag
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: open-webui
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
      ports:
        - port: 8080
```

---

### 12.10 Monitoring & Observability

**Stack:** Azure Monitor Container Insights + Azure Managed Prometheus + Azure Managed Grafana

```yaml
# k8s/base/configmap.yaml (monitoring-related env vars)
# Celery workers expose Prometheus metrics via celery-exporter
# FastAPI exposes /metrics via prometheus-fastapi-instrumentator

# Enable Prometheus scraping via pod annotations
# (Azure Managed Prometheus auto-discovers pods with these annotations)

# In deployment templates:
# metadata:
#   annotations:
#     prometheus.io/scrape: "true"
#     prometheus.io/port: "9090"
#     prometheus.io/path: "/metrics"
```

**Key metrics to monitor:**

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Celery queue length (`rag.query`) | Redis / KEDA metrics | > 20 tasks for > 2 min |
| Celery queue length (`rag.doc_gen`) | Redis / KEDA metrics | > 10 tasks for > 5 min |
| Request latency P95 | FastAPI `/metrics` | > 15s |
| SSE connection count | NGINX Ingress metrics | > 500 concurrent |
| Redis memory usage | Azure Cache metrics | > 80% |
| PostgreSQL active connections | Azure DB metrics | > 250 |
| Pod restart count | Container Insights | > 3 in 10 min |
| Node CPU utilization | Container Insights | > 85% sustained |
| LLM API error rate | Application metrics | > 5% |
| Doc-gen task failure rate | Celery metrics | > 10% |

**Grafana dashboards:**
1. **Overview** — request rate, latency percentiles, active users, queue depths
2. **Worker Health** — per-worker CPU/memory, task throughput, failure rate
3. **Infrastructure** — node pool utilization, Redis memory, PostgreSQL connections
4. **LLM Costs** — token usage per model, requests per provider, estimated cost/hour

---

### 12.11 ConfigMap (Non-Secret Environment)

```yaml
# k8s/base/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: better-rag-config
  namespace: better-rag
data:
  # LLM Configuration
  LLM_PROVIDER: "azure_openai"
  LLM_EXPENSIVE_MODEL: "gpt-4o"
  LLM_CHEAP_MODEL: "gpt-4o-mini"
  AZURE_OPENAI_ENDPOINT: "https://better-rag-openai.openai.azure.com/"

  # SharePoint
  GRAPH_TENANT_ID: "${TENANT_ID}"
  GRAPH_CLIENT_ID: "${GRAPH_APP_CLIENT_ID}"

  # OCR
  OCR_PROVIDER: "azure_di"
  OCR_AZURE_ENDPOINT: "https://better-rag-di.cognitiveservices.azure.com/"

  # Embedding
  EMBEDDING_AZURE_ENDPOINT: "https://better-rag-openai.openai.azure.com/"
  EMBEDDING_AZURE_DEPLOYMENT: "text-embedding-3-large"
  EMBEDDING_DIMENSIONS: "1536"

  # Chunking
  CHUNK_TARGET_TOKENS: "450"
  CHUNK_MAX_TOKENS: "600"
  CHUNK_OVERLAP_TOKENS: "60"

  # Databases (connection strings without passwords)
  PGVECTOR_HOST: "${PG_HOST}"
  PGVECTOR_PORT: "5432"
  PGVECTOR_DATABASE: "betterrag"
  NEO4J_URI: "bolt://better-rag-neo4j:7687"
  REDIS_HOST: "${REDIS_HOST}"
  REDIS_PORT: "6380"
  REDIS_SSL: "true"

  # Celery (Redis db=1 as broker)
  CELERY_BROKER_URL: "rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/1"

  # Redis Streams (db=2)
  REDIS_STREAMS_URL: "rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/2"

  # Cache (db=3)
  REDIS_CACHE_URL: "rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/3"
  CACHE_HYDE_TTL: "3600"
  CACHE_RBAC_TTL: "300"
  CACHE_QUERY_TTL: "120"

  # LangGraph Checkpoints (db=4)
  LANGGRAPH_CHECKPOINT_REDIS_URL: "rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/4"

  # Connection pools
  PGVECTOR_POOL_SIZE: "20"
  PGVECTOR_POOL_MAX_OVERFLOW: "10"

  # Worker tuning
  QUERY_WORKER_CONCURRENCY: "10"
  DOCGEN_WORKER_CONCURRENCY: "2"
  INGESTION_WORKER_CONCURRENCY: "4"

  # LLM rate limits
  LLM_RATE_LIMIT_RPM: "500"
  LLM_CONCURRENT_REQUESTS: "50"

  # Blob Storage
  BLOB_ACCOUNT_URL: "https://${STORAGE_ACCOUNT}.blob.core.windows.net"
  BLOB_CONTAINER_NAME: "documents"
```

---

### 12.12 Docker Images (ACR)

Two container images, built in CI and pushed to ACR:

| Image | Base | Contents | Used By |
|-------|------|----------|---------|
| `betterragacr.azurecr.io/better-rag-api` | `python:3.12-slim` | Python app, uvicorn, gevent | API server, query-worker, celery-beat |
| `betterragacr.azurecr.io/better-rag-docgen` | `python:3.12-slim` + Node.js 20 + LibreOffice + Poppler | Python app + doc-gen toolchain | docgen-worker, ingestion-worker |

```dockerfile
# Dockerfile (API / query worker / celery-beat)
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

COPY src/ src/
COPY config/ config/
COPY alembic/ alembic/

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```dockerfile
# Dockerfile.docgen (doc-gen worker / ingestion worker)
FROM python:3.12-slim

# Install system dependencies for document generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    poppler-utils \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pptxgenjs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

COPY src/ src/
COPY config/ config/

CMD ["celery", "-A", "src.celery_app", "worker", "--loglevel=info"]
```

---

### 12.13 CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/deploy.yml
name: Build & Deploy to AKS

on:
  push:
    branches: [main]

env:
  ACR_NAME: betterragacr
  AKS_CLUSTER: better-rag-aks
  AKS_RG: better-rag-rg
  NAMESPACE: better-rag

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        image:
          - { name: better-rag-api, dockerfile: Dockerfile }
          - { name: better-rag-docgen, dockerfile: Dockerfile.docgen }
    steps:
      - uses: actions/checkout@v4

      - name: Login to ACR
        uses: azure/docker-login@v2
        with:
          login-server: ${{ env.ACR_NAME }}.azurecr.io
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}

      - name: Build and push
        run: |
          docker build -f ${{ matrix.image.dockerfile }} \
            -t ${{ env.ACR_NAME }}.azurecr.io/${{ matrix.image.name }}:${{ github.sha }} \
            -t ${{ env.ACR_NAME }}.azurecr.io/${{ matrix.image.name }}:latest .
          docker push ${{ env.ACR_NAME }}.azurecr.io/${{ matrix.image.name }} --all-tags

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Azure Login
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Set AKS context
        uses: azure/aks-set-context@v4
        with:
          cluster-name: ${{ env.AKS_CLUSTER }}
          resource-group: ${{ env.AKS_RG }}

      - name: Deploy with Kustomize
        run: |
          cd k8s/overlays/prod
          kustomize edit set image \
            betterragacr.azurecr.io/better-rag-api=betterragacr.azurecr.io/better-rag-api:${{ github.sha }} \
            betterragacr.azurecr.io/better-rag-docgen=betterragacr.azurecr.io/better-rag-docgen:${{ github.sha }}
          kubectl apply -k .

      - name: Verify rollout
        run: |
          kubectl rollout status deployment/better-rag-api -n ${{ env.NAMESPACE }} --timeout=300s
          kubectl rollout status deployment/query-worker -n ${{ env.NAMESPACE }} --timeout=300s
          kubectl rollout status deployment/docgen-worker -n ${{ env.NAMESPACE }} --timeout=300s
```

---

### 12.14 Capacity Planning (AKS)

| Component | Instances | Per-Instance Resources | Node Pool | Total Resources |
|-----------|-----------|----------------------|-----------|-----------------|
| NGINX Ingress | 2 pods | 1 CPU, 1GB | system | 2 CPU, 2GB |
| KEDA + cert-manager | 2+2 pods | 0.5 CPU, 0.5GB | system | 2 CPU, 2GB |
| Open WebUI | 3 pods | 2 CPU, 4GB | apppool | 6 CPU, 12GB |
| FastAPI API | 3-8 pods | 2 CPU, 2GB | apppool | 6-16 CPU, 6-16GB |
| Query Worker | 3-8 pods | 4 CPU, 8GB | apppool | 12-32 CPU, 24-64GB |
| Celery Beat | 1 pod | 0.5 CPU, 0.5GB | apppool | 0.5 CPU, 0.5GB |
| Doc-Gen Worker | 2-6 pods | 4 CPU, 8GB | heavypool | 8-24 CPU, 16-48GB |
| Ingestion Worker | 1-3 pods | 4 CPU, 8GB | heavypool | 4-12 CPU, 8-24GB |
| Neo4j | 1 pod | 4 CPU, 8GB | datapool | 4 CPU, 8GB |

**Node Pool Sizing Summary:**

| Pool | VM SKU | Min Nodes | Max Nodes | Total Resources (min) |
|------|--------|-----------|-----------|----------------------|
| system | Standard_D4s_v5 (4C/16G) | 3 | 3 | 12 CPU, 48GB |
| apppool | Standard_D8s_v5 (8C/32G) | 3 | 6 | 24-48 CPU, 96-192GB |
| heavypool | Standard_D8s_v5 (8C/32G) | 2 | 4 | 16-32 CPU, 64-128GB |
| datapool | Standard_E8s_v5 (8C/64G) | 2 | 2 | 16 CPU, 128GB |

**Azure Managed Services:**

| Service | SKU | Cost Driver |
|---------|-----|-------------|
| Azure DB for PostgreSQL | GP_Standard_D4s_v3 (4C/16G) | Compute + storage |
| Azure Cache for Redis | Premium P1 (6GB) | Cache size |
| Azure Container Registry | Standard | Storage + builds |
| Azure Key Vault | Standard | Operations |
| Azure Managed Grafana | Essential | Per-user |

**Estimated monthly Azure cost (steady state, 800 users):**
- AKS compute (10 nodes baseline): ~$3,200/mo
- Azure PostgreSQL: ~$400/mo
- Azure Redis P1: ~$300/mo
- Azure Blob Storage: ~$50/mo
- Azure OpenAI / Anthropic API: Variable (dominant cost)
- Monitoring + misc: ~$150/mo
- **Total infrastructure (excl. LLM API): ~$4,100/mo**

---

### 12.15 Key Design Decisions (AKS)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PostgreSQL | Azure Managed (Flexible Server) | Auto-backups, HA, patching, pgvector support. Eliminates StatefulSet ops complexity. |
| Redis | Azure Cache for Redis (Premium) | Persistence, TLS, private endpoint, monitoring built-in. Premium tier supports multiple databases (db0-4). |
| Neo4j | Self-hosted (Helm) | No Azure managed Neo4j. Official Helm chart + Azure Disk PVC. Community edition sufficient. |
| Ingress | NGINX (not AGIC) | Better SSE/WebSocket support, fine-grained proxy annotations, faster config reconciliation. |
| Secrets | Key Vault + CSI + Workload Identity | Zero stored credentials in cluster. Pods federate with Entra ID via ServiceAccount tokens. |
| Autoscaling (workers) | KEDA on Redis list length | Event-driven scaling based on actual queue depth. Scales to zero possible (but min=1 for latency). |
| Autoscaling (API) | HPA on CPU | API is stateless; CPU correlates well with SSE connection count. |
| Node pools | 4 pools (system/app/heavy/data) | Isolation: system pods protected, heavy workers don't starve app pods, Neo4j gets memory-optimized nodes. |
| TLS | cert-manager + Let's Encrypt + Azure DNS | Automatic provisioning and renewal. DNS-01 challenge via Workload Identity (no stored DNS credentials). |
| CI/CD | GitHub Actions + Kustomize | Image tag promotion via `kustomize edit set image`. No Helm templating complexity for app deployments. |
| Monitoring | Azure Monitor + Managed Prometheus + Grafana | Zero cluster-side ops. Native AKS integration. 18-month retention. |
| Docker images | 2 images (api vs docgen) | Separation of concerns: API image is small (~300MB), docgen image is large (~1.5GB) with Node.js + LibreOffice. |

---

### 12.16 Updated Implementation Phases

Add after Phase 9B (Open WebUI Integration):

### Phase 11 — Infrastructure Provisioning (AKS)
59. Terraform modules: VNet, AKS cluster, node pools
60. Terraform modules: Azure DB for PostgreSQL (pgvector enabled), Azure Cache for Redis
61. Terraform modules: Azure Key Vault, ACR, Blob Storage, Managed Prometheus + Grafana
62. Terraform modules: Workload Identity (UAMI + federated credentials + role assignments)
63. Terraform modules: Private endpoints (PostgreSQL, Redis, Blob Storage)
64. Terraform modules: Azure DNS zone + records

### Phase 12 — Kubernetes Deployment
65. Dockerfiles (api + docgen) and ACR image build pipeline
66. Kustomize base: namespace, ConfigMap, SecretProviderClass, ServiceAccount
67. API server Deployment + Service + HPA
68. Worker Deployments (query, docgen, ingestion) + KEDA ScaledObjects
69. Celery Beat Deployment
70. Helm releases: NGINX Ingress, cert-manager, KEDA
71. Helm releases: Open WebUI (with Entra ID SSO config)
72. Helm release: Neo4j (with Azure Disk PVC)
73. Ingress + ClusterIssuer + TLS
74. Network Policies (default-deny + allow rules)
75. GitHub Actions CI/CD pipeline
76. Monitoring dashboards (Grafana) + alerting rules

### Phase 13 (Updated) — Testing & Hardening
77. Smoke test: end-to-end through Ingress → Open WebUI → API → Worker → response
78. Load test: simulate 800 users with k6, verify KEDA scaling behavior
79. Failover test: kill worker pods, verify task recovery via LangGraph checkpoints
80. Security scan: network policies audit, no public endpoints, Key Vault rotation test
81. Previous testing items (unit tests, integration tests, retrieval quality evaluation)

---

### 12.17 Updated Project Structure

```
better-rag/
├── ...                              # (existing src/, config/, etc.)
├── Dockerfile                       # API / query-worker / celery-beat
├── Dockerfile.docgen                # Doc-gen / ingestion worker
├── docker-compose.yml               # Local development
├── docker-compose.production.yml    # Docker Compose production (kept for reference)
│
├── infra/
│   └── terraform/
│       ├── main.tf                  # AKS cluster, node pools
│       ├── databases.tf             # PostgreSQL Flexible Server, Redis Cache
│       ├── identity.tf              # Workload Identity, UAMI, federated creds
│       ├── keyvault.tf              # Key Vault + secrets
│       ├── networking.tf            # VNet, subnets, private endpoints, NSGs
│       ├── monitoring.tf            # Prometheus workspace, Grafana
│       ├── acr.tf                   # Container Registry
│       ├── dns.tf                   # Azure DNS zone
│       ├── variables.tf
│       ├── outputs.tf
│       └── terraform.tfvars.example
│
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   ├── workload-identity.yaml
│   │   ├── configmap.yaml
│   │   ├── api/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── hpa.yaml
│   │   ├── workers/
│   │   │   ├── query-worker.yaml
│   │   │   ├── docgen-worker.yaml
│   │   │   ├── ingestion-worker.yaml
│   │   │   └── celery-beat.yaml
│   │   ├── open-webui/
│   │   │   ├── values.yaml
│   │   │   └── pipe-function-cm.yaml
│   │   ├── neo4j/
│   │   │   └── values.yaml
│   │   ├── ingress/
│   │   │   ├── ingress.yaml
│   │   │   └── cluster-issuer.yaml
│   │   └── network-policies/
│   │       ├── default-deny.yaml
│   │       ├── allow-api-to-redis.yaml
│   │       ├── allow-workers-to-stores.yaml
│   │       ├── allow-owui-to-api.yaml
│   │       └── allow-ingress.yaml
│   ├── overlays/
│   │   ├── dev/
│   │   │   ├── kustomization.yaml
│   │   │   └── patches/
│   │   └── prod/
│   │       ├── kustomization.yaml
│   │       └── patches/
│   └── helm-releases/
│       ├── nginx-ingress.yaml
│       ├── cert-manager.yaml
│       ├── keda.yaml
│       ├── open-webui.yaml
│       └── neo4j.yaml
│
└── .github/
    └── workflows/
        └── deploy.yml               # Build → Push to ACR → Deploy to AKS
```

---

## Section 13: SharePoint CRUD Poller — Detailed Implementation

This section expands the high-level `DeltaSyncManager` from Section 1 into a complete, production-grade poller that detects file Creates, Updates, and Deletes in SharePoint/OneDrive document libraries and routes each change through the appropriate pipeline.

### 13.1 Architecture: Webhooks + Delta Query (Hybrid)

Microsoft's recommended pattern (per their [scale guidance](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance)) is **webhooks as triggers + delta query for change retrieval**:

```
┌──────────────────────────────────────────────────────────────────┐
│                Microsoft Graph API                                │
│                                                                   │
│  ┌────────────┐    ┌──────────────────────────────────┐          │
│  │ Webhooks   │    │  Delta Query API                  │          │
│  │ /subscriptions  │  GET /drives/{id}/root/delta      │          │
│  │  (push)    │    │  (pull, paginated, persistent)    │          │
│  └──────┬─────┘    └───────────────┬──────────────────┘          │
└─────────┼──────────────────────────┼─────────────────────────────┘
          │                          │
          │  POST notification       │  Pages of driveItem changes
          ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   better-rag Backend                              │
│                                                                   │
│  ┌──────────────────┐     ┌──────────────────────────────────┐  │
│  │ Webhook Endpoint  │────▶│  Delta Sync Task (Celery)        │  │
│  │ POST /webhooks/   │     │                                  │  │
│  │  graph/notify     │     │  1. Load delta_token from DB     │  │
│  └──────────────────┘     │  2. GET /drives/{id}/root/delta  │  │
│                            │  3. Paginate via @odata.nextLink │  │
│  ┌──────────────────┐     │  4. Classify: Create/Update/Del  │  │
│  │ Celery Beat       │────▶│  5. Route to CRUD handlers       │  │
│  │ (every 15 min     │     │  6. Save new delta_token         │  │
│  │  + daily full)    │     └──────────────┬───────────────────┘  │
│  └──────────────────┘                     │                      │
│                                           ▼                      │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                 CRUD Handlers                               │  │
│  │                                                             │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │  │
│  │  │ on_create│  │ on_update│  │ on_delete│                 │  │
│  │  │          │  │          │  │          │                 │  │
│  │  │ Download │  │ Download │  │ Remove   │                 │  │
│  │  │ → Stage  │  │ → Stage  │  │  chunks  │                 │  │
│  │  │ → OCR    │  │ → OCR    │  │  embeddings               │  │
│  │  │ → Chunk  │  │ → Chunk  │  │  graph nodes              │  │
│  │  │ → Embed  │  │ → Embed  │  │  blob     │                 │  │
│  │  │ → RBAC   │  │ → RBAC   │  │  metadata │                 │  │
│  │  │ → Graph  │  │(replace) │  │          │                 │  │
│  │  └──────────┘  └──────────┘  └──────────┘                 │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Why both mechanisms?**
- **Webhooks** tell us *when* to check — avoids wasteful polling every 15 min when nothing changed
- **Delta query** tells us *what* changed — the webhook payload only says "something changed in this drive", not which files
- **Celery Beat fallback** ensures no changes are missed if a webhook is dropped (Microsoft doesn't guarantee delivery)
- **Daily full reconciliation** catches anything missed by both (recommended by Microsoft: no more than once/day)

---

### 13.2 Database Models

```python
# src/models/document.py

from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Text, Integer, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from src.storage.db import Base


class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"           # Queued for processing
    DOWNLOADING = "downloading"   # Fetching from SharePoint / Blob
    PROCESSING = "processing"     # OCR + parsing + metadata
    CHUNKING = "chunking"         # Adaptive chunking
    EMBEDDING = "embedding"       # Generating embeddings
    INDEXING = "indexing"          # Writing to pgvector + Neo4j
    COMPLETED = "completed"       # Fully indexed and searchable
    FAILED = "failed"             # Processing failed (see error_message)
    DELETING = "deleting"         # Removal in progress
    DELETED = "deleted"           # Soft-deleted (tombstone)


class ChangeType(str, enum.Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    PERMISSION_CHANGED = "permission_changed"


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # SharePoint identity
    drive_id = Column(String, nullable=False, index=True)
    drive_item_id = Column(String, nullable=False, index=True)
    site_id = Column(String, nullable=False, index=True)

    # File metadata
    name = Column(String, nullable=False)
    file_type = Column(String, nullable=False)           # pdf, docx, pptx, xlsx
    size_bytes = Column(Integer)
    mime_type = Column(String)
    sharepoint_url = Column(Text, nullable=False)        # webUrl for citations
    parent_path = Column(Text)                           # Path within drive

    # Content tracking
    ctag = Column(String)                                # Content eTag — changes when file content changes
    etag = Column(String)                                # Entity eTag — changes on any metadata/content change
    last_modified_graph = Column(DateTime(timezone=True)) # lastModifiedDateTime from Graph API
    content_hash = Column(String)                        # SHA-256 of downloaded file (dedup guard)

    # Processing state
    status = Column(Enum(ProcessingStatus), default=ProcessingStatus.PENDING, nullable=False)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    processing_started_at = Column(DateTime(timezone=True))
    processing_completed_at = Column(DateTime(timezone=True))

    # Blob staging
    blob_path = Column(String)                           # Azure Blob path after download

    # LLM-derived metadata
    summary = Column(Text)
    department = Column(String)
    content_type_tag = Column(String)                    # policy, report, presentation, etc.
    topics = Column(JSON)                                # ["budgeting", "Q3", ...]
    language = Column(String, default="en")

    # Audit
    created_at = Column(DateTime(timezone=True), server_default="now()")
    updated_at = Column(DateTime(timezone=True), server_default="now()", onupdate="now()")
    created_by = Column(String)                          # Graph API author
    modified_by = Column(String)                         # Graph API lastModifiedBy

    # Relationships
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    permissions = relationship("DocumentPermission", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_documents_drive_item", "drive_id", "drive_item_id", unique=True),
        Index("ix_documents_status", "status"),
    )


class SyncCursor(Base):
    """Stores delta tokens per drive for incremental sync."""
    __tablename__ = "sync_cursors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(String, nullable=False)
    drive_id = Column(String, nullable=False)
    delta_token = Column(Text, nullable=False)           # @odata.deltaLink URL (complete)
    last_sync_at = Column(DateTime(timezone=True))
    token_obtained_at = Column(DateTime(timezone=True))  # Track token age (expire ~30 days)
    full_crawl_completed = Column(DateTime(timezone=True)) # Last full enumeration
    items_processed = Column(Integer, default=0)          # Cumulative count

    __table_args__ = (
        Index("ix_sync_cursor_drive", "site_id", "drive_id", unique=True),
    )


class SyncEvent(Base):
    """Audit log of every detected change. Useful for debugging and replay."""
    __tablename__ = "sync_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drive_id = Column(String, nullable=False)
    drive_item_id = Column(String, nullable=False)
    change_type = Column(Enum(ChangeType), nullable=False)
    file_name = Column(String)
    detected_at = Column(DateTime(timezone=True), server_default="now()")
    processed_at = Column(DateTime(timezone=True))
    celery_task_id = Column(String)                      # Links to the Celery task that handled it
    error_message = Column(Text)
    raw_delta_item = Column(JSON)                        # Full driveItem JSON from delta (for replay)

    __table_args__ = (
        Index("ix_sync_events_detected", "detected_at"),
    )
```

---

### 13.3 Delta Sync Manager (Core Poller)

```python
# src/connectors/delta_sync.py

import structlog
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.connectors.graph_client import GraphClientFactory
from src.models.document import SyncCursor, SyncEvent, Document, ChangeType, ProcessingStatus

logger = structlog.get_logger()

# File types we process (everything else is ignored)
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


class DeltaSyncManager:
    """
    Polls Microsoft Graph delta API to detect CRUD changes in SharePoint drives.

    Usage:
        manager = DeltaSyncManager(graph_client, db_session)
        changes = await manager.sync_drive(site_id, drive_id)
        # changes = [SyncEvent(change_type=CREATED, ...), SyncEvent(change_type=DELETED, ...), ...]
    """

    def __init__(self, graph_client, db: AsyncSession):
        self.graph = graph_client
        self.db = db

    async def sync_drive(self, site_id: str, drive_id: str) -> list[SyncEvent]:
        """
        Run a delta sync for a single drive. Returns list of classified changes.

        Steps:
        1. Load existing delta_token (or None for initial crawl)
        2. Call delta API, paginate through all @odata.nextLink pages
        3. Classify each driveItem as Create/Update/Delete
        4. Persist SyncEvents
        5. Save new delta_token from @odata.deltaLink
        """
        cursor = await self._get_or_create_cursor(site_id, drive_id)
        delta_url = cursor.delta_token  # Full deltaLink URL, or None

        all_changes: list[SyncEvent] = []
        items_in_page = 0
        page_count = 0

        try:
            # If no delta_token, this is the initial crawl
            if delta_url is None:
                logger.info("delta_sync.initial_crawl", drive_id=drive_id)
                response = await self.graph.get(
                    f"/drives/{drive_id}/root/delta",
                    params={"$select": "id,name,file,folder,deleted,parentReference,"
                                       "lastModifiedDateTime,lastModifiedBy,createdBy,"
                                       "webUrl,cTag,eTag,size,content.downloadUrl"},
                )
            else:
                # Check token age — if >25 days, force a full re-crawl
                if cursor.token_obtained_at:
                    token_age = datetime.now(timezone.utc) - cursor.token_obtained_at
                    if token_age > timedelta(days=25):
                        logger.warn("delta_sync.token_expiring", drive_id=drive_id,
                                    age_days=token_age.days)
                        response = await self.graph.get(
                            f"/drives/{drive_id}/root/delta",
                        )
                    else:
                        response = await self.graph.get(delta_url)
                else:
                    response = await self.graph.get(delta_url)

            # Paginate through all pages
            while True:
                page_count += 1
                data = response.json()
                items = data.get("value", [])
                items_in_page += len(items)

                logger.debug("delta_sync.page", drive_id=drive_id,
                             page=page_count, items=len(items))

                # Classify each item
                for item in items:
                    change = await self._classify_and_record(drive_id, item)
                    if change:
                        all_changes.append(change)

                # Check for more pages
                next_link = data.get("@odata.nextLink")
                delta_link = data.get("@odata.deltaLink")

                if next_link:
                    response = await self.graph.get(next_link)
                elif delta_link:
                    # Save the new delta token
                    cursor.delta_token = delta_link
                    cursor.last_sync_at = datetime.now(timezone.utc)
                    cursor.token_obtained_at = datetime.now(timezone.utc)
                    cursor.items_processed += items_in_page
                    if delta_url is None:
                        cursor.full_crawl_completed = datetime.now(timezone.utc)
                    break
                else:
                    logger.error("delta_sync.no_link", drive_id=drive_id)
                    break

            await self.db.commit()
            logger.info("delta_sync.completed", drive_id=drive_id,
                        pages=page_count, changes=len(all_changes),
                        total_items=items_in_page)

        except Exception as e:
            await self.db.rollback()
            # Handle HTTP 410 Gone — token expired, need full re-crawl
            if hasattr(e, 'status_code') and e.status_code == 410:
                logger.warn("delta_sync.token_expired", drive_id=drive_id)
                cursor.delta_token = None  # Force full re-crawl on next run
                await self.db.commit()
            else:
                logger.error("delta_sync.failed", drive_id=drive_id, error=str(e))
            raise

        return all_changes

    async def _classify_and_record(self, drive_id: str, item: dict) -> SyncEvent | None:
        """
        Classify a single driveItem from the delta response as Create, Update, or Delete.

        Classification rules:
        - Has "deleted" facet → DELETE
        - Has "folder" facet → SKIP (we don't index folders)
        - Has "file" facet + not in our DB → CREATE
        - Has "file" facet + in our DB + cTag changed → UPDATE (content changed)
        - Has "file" facet + in our DB + cTag same but eTag changed → PERMISSION/METADATA change
        - Unsupported file extension → SKIP
        """
        item_id = item.get("id")
        name = item.get("name", "")

        # --- DELETE ---
        if "deleted" in item:
            existing = await self._find_document(drive_id, item_id)
            if existing:
                event = SyncEvent(
                    drive_id=drive_id,
                    drive_item_id=item_id,
                    change_type=ChangeType.DELETED,
                    file_name=existing.name,
                    raw_delta_item=item,
                )
                self.db.add(event)
                return event
            # Item deleted but we never tracked it — skip
            return None

        # --- SKIP folders ---
        if "folder" in item:
            return None

        # --- SKIP non-file items (notebooks, etc.) ---
        if "file" not in item:
            return None

        # --- SKIP unsupported file types ---
        ext = _get_extension(name)
        if ext not in SUPPORTED_EXTENSIONS:
            return None

        # --- CREATE or UPDATE ---
        existing = await self._find_document(drive_id, item_id)

        if existing is None:
            # CREATE — new file
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.CREATED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        # File exists in our DB — check what changed
        new_ctag = item.get("cTag")
        new_etag = item.get("eTag")

        if new_ctag and new_ctag != existing.ctag:
            # Content changed → UPDATE (re-process everything)
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.UPDATED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        if new_etag and new_etag != existing.etag:
            # Only metadata/permissions changed (no content change)
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.PERMISSION_CHANGED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        # Same cTag and eTag — no meaningful change (delta can return items
        # in the parent hierarchy even if they didn't change)
        return None

    async def _find_document(self, drive_id: str, drive_item_id: str) -> Document | None:
        result = await self.db.execute(
            select(Document).where(
                Document.drive_id == drive_id,
                Document.drive_item_id == drive_item_id,
                Document.status != ProcessingStatus.DELETED,
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_cursor(self, site_id: str, drive_id: str) -> SyncCursor:
        result = await self.db.execute(
            select(SyncCursor).where(
                SyncCursor.site_id == site_id,
                SyncCursor.drive_id == drive_id,
            )
        )
        cursor = result.scalar_one_or_none()
        if cursor is None:
            cursor = SyncCursor(
                site_id=site_id,
                drive_id=drive_id,
                delta_token=None,
            )
            self.db.add(cursor)
            await self.db.flush()
        return cursor


def _get_extension(filename: str) -> str:
    """Extract lowercase file extension."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""
```

---

### 13.4 CRUD Handlers

```python
# src/connectors/change_handlers.py

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from src.models.document import (
    Document, ProcessingStatus, ChangeType, SyncEvent,
    Chunk, DocumentPermission
)
from src.connectors.graph_client import GraphClientFactory
from src.storage.blob_store import AzureBlobStore
from src.storage.vector_store import PgVectorStore
from src.knowledge_graph.builder import GraphBuilder
from src.connectors.permissions import PermissionResolver
from src.celery_app import run_ingestion_task

logger = structlog.get_logger()


class ChangeHandler:
    """
    Routes classified SyncEvents to the appropriate CRUD handler.
    Each handler dispatches a Celery task for the actual heavy work.
    """

    def __init__(self, db: AsyncSession, graph_client):
        self.db = db
        self.graph = graph_client

    async def process_changes(self, changes: list[SyncEvent]):
        """
        Process a batch of classified changes.
        Groups by type and dispatches appropriately.
        """
        stats = {"created": 0, "updated": 0, "deleted": 0, "permission": 0}

        for change in changes:
            try:
                if change.change_type == ChangeType.CREATED:
                    await self._handle_create(change)
                    stats["created"] += 1

                elif change.change_type == ChangeType.UPDATED:
                    await self._handle_update(change)
                    stats["updated"] += 1

                elif change.change_type == ChangeType.DELETED:
                    await self._handle_delete(change)
                    stats["deleted"] += 1

                elif change.change_type == ChangeType.PERMISSION_CHANGED:
                    await self._handle_permission_change(change)
                    stats["permission"] += 1

                change.processed_at = datetime.now(timezone.utc)

            except Exception as e:
                change.error_message = str(e)
                logger.error("change_handler.failed",
                             change_type=change.change_type,
                             item_id=change.drive_item_id, error=str(e))

        await self.db.commit()
        logger.info("change_handler.batch_complete", **stats)
        return stats

    # ────────────────────────────────────────────────────
    # CREATE — New file detected in SharePoint
    # ────────────────────────────────────────────────────

    async def _handle_create(self, event: SyncEvent):
        """
        New file detected. Create a Document record and dispatch ingestion task.

        Flow:
        1. Extract metadata from the delta driveItem JSON
        2. Create Document record (status=PENDING)
        3. Dispatch Celery ingestion task (download → OCR → chunk → embed → graph)
        """
        item = event.raw_delta_item
        parent_ref = item.get("parentReference", {})

        doc = Document(
            drive_id=event.drive_id,
            drive_item_id=event.drive_item_id,
            site_id=parent_ref.get("siteId", ""),
            name=item.get("name", ""),
            file_type=_get_extension(item.get("name", "")),
            size_bytes=item.get("size"),
            mime_type=item.get("file", {}).get("mimeType"),
            sharepoint_url=item.get("webUrl", ""),
            parent_path=parent_ref.get("path", ""),
            ctag=item.get("cTag"),
            etag=item.get("eTag"),
            last_modified_graph=item.get("lastModifiedDateTime"),
            created_by=_extract_user(item.get("createdBy")),
            modified_by=_extract_user(item.get("lastModifiedBy")),
            status=ProcessingStatus.PENDING,
        )
        self.db.add(doc)
        await self.db.flush()  # Get the doc.id

        # Dispatch to Celery ingestion queue
        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "create",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id

        logger.info("change_handler.create",
                     doc_id=str(doc.id), name=doc.name, task_id=task.id)

    # ────────────────────────────────────────────────────
    # UPDATE — Existing file modified (content changed)
    # ────────────────────────────────────────────────────

    async def _handle_update(self, event: SyncEvent):
        """
        File content changed. Re-process the entire document.

        Flow:
        1. Find existing Document record
        2. Update metadata fields (cTag, eTag, lastModified, etc.)
        3. Set status=PENDING (resets the processing pipeline)
        4. Dispatch Celery ingestion task with operation="update"
           - The ingestion task will:
             a. Download new version
             b. Re-run OCR + parsing + metadata extraction
             c. Delete OLD chunks, embeddings, graph nodes
             d. Create NEW chunks, embeddings, graph nodes
             e. Preserve the same Document.id (so citations stay valid
                for in-progress conversations)
        """
        item = event.raw_delta_item
        doc = await self._find_document(event.drive_id, event.drive_item_id)

        if doc is None:
            # Race condition: update arrived but we don't have the doc
            # Treat as a create
            logger.warn("change_handler.update_as_create",
                        item_id=event.drive_item_id)
            event.change_type = ChangeType.CREATED
            await self._handle_create(event)
            return

        # Update metadata
        doc.name = item.get("name", doc.name)
        doc.size_bytes = item.get("size", doc.size_bytes)
        doc.ctag = item.get("cTag", doc.ctag)
        doc.etag = item.get("eTag", doc.etag)
        doc.last_modified_graph = item.get("lastModifiedDateTime", doc.last_modified_graph)
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by
        doc.sharepoint_url = item.get("webUrl", doc.sharepoint_url)
        doc.status = ProcessingStatus.PENDING
        doc.error_message = None
        doc.retry_count = 0

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "update",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id

        logger.info("change_handler.update",
                     doc_id=str(doc.id), name=doc.name, task_id=task.id)

    # ────────────────────────────────────────────────────
    # DELETE — File removed from SharePoint
    # ────────────────────────────────────────────────────

    async def _handle_delete(self, event: SyncEvent):
        """
        File deleted from SharePoint. Remove all associated data.

        Flow:
        1. Find Document record
        2. Set status=DELETING
        3. Delete chunks from pgvector (embeddings)
        4. Delete nodes/relationships from Neo4j
        5. Delete staged blob from Azure Blob Storage
        6. Delete permission records
        7. Set status=DELETED (soft delete — keep tombstone for audit)
        """
        doc = await self._find_document(event.drive_id, event.drive_item_id)

        if doc is None:
            logger.debug("change_handler.delete_not_found",
                         item_id=event.drive_item_id)
            return

        doc.status = ProcessingStatus.DELETING

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "delete",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id

        logger.info("change_handler.delete",
                     doc_id=str(doc.id), name=doc.name, task_id=task.id)

    # ────────────────────────────────────────────────────
    # PERMISSION CHANGED — eTag changed but cTag didn't
    # ────────────────────────────────────────────────────

    async def _handle_permission_change(self, event: SyncEvent):
        """
        Only permissions/metadata changed, not content.
        No need to re-process the document — just refresh RBAC.

        Flow:
        1. Find Document record
        2. Re-fetch permissions from Graph API
        3. Update document_user_access table
        4. Invalidate RBAC cache for affected users
        """
        doc = await self._find_document(event.drive_id, event.drive_item_id)

        if doc is None:
            return

        # Update metadata only
        item = event.raw_delta_item
        doc.etag = item.get("eTag", doc.etag)
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by

        # Dispatch lightweight permission refresh (not full ingestion)
        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "refresh_permissions",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id

        logger.info("change_handler.permission_change",
                     doc_id=str(doc.id), name=doc.name, task_id=task.id)

    async def _find_document(self, drive_id: str, drive_item_id: str) -> Document | None:
        result = await self.db.execute(
            select(Document).where(
                Document.drive_id == drive_id,
                Document.drive_item_id == drive_item_id,
                Document.status != ProcessingStatus.DELETED,
            )
        )
        return result.scalar_one_or_none()


def _extract_user(user_dict: dict | None) -> str | None:
    if not user_dict:
        return None
    user = user_dict.get("user", {})
    return user.get("email") or user.get("displayName")


def _get_extension(filename: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""
```

---

### 13.5 Celery Ingestion Task (CRUD Router)

```python
# src/celery_app.py — Ingestion task that handles all CRUD operations

@celery_app.task(
    bind=True,
    queue="rag.ingestion",
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=600,     # 10 min soft limit
    time_limit=900,          # 15 min hard kill
)
def run_ingestion_task(self, document_id, drive_id, drive_item_id, operation):
    """
    Unified ingestion task that routes to the appropriate CRUD handler.

    Operations:
    - "create":              Download → Stage → OCR → Chunk → Embed → RBAC → Graph
    - "update":              Same as create, but first deletes old chunks/embeddings
    - "delete":              Remove chunks, embeddings, graph nodes, blob, permissions
    - "refresh_permissions": Re-fetch permissions only (no content re-processing)
    """
    import asyncio
    try:
        asyncio.run(_run_ingestion(document_id, drive_id, drive_item_id, operation))
    except Exception as exc:
        logger.error("ingestion_task.failed",
                     doc_id=document_id, operation=operation, error=str(exc))
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _run_ingestion(document_id, drive_id, drive_item_id, operation):
    async with get_db_session() as db:
        doc = await db.get(Document, document_id)
        if not doc:
            logger.warn("ingestion.doc_not_found", doc_id=document_id)
            return

        graph_client = await GraphClientFactory.create()
        blob_store = AzureBlobStore()
        vector_store = PgVectorStore()
        graph_builder = GraphBuilder()
        permission_resolver = PermissionResolver(graph_client, db)

        if operation == "create":
            await _ingest_new_document(
                db, doc, graph_client, blob_store, vector_store,
                graph_builder, permission_resolver
            )

        elif operation == "update":
            # Delete old artifacts first
            await _delete_document_artifacts(db, doc, vector_store, graph_builder, blob_store)
            # Then re-ingest
            await _ingest_new_document(
                db, doc, graph_client, blob_store, vector_store,
                graph_builder, permission_resolver
            )

        elif operation == "delete":
            await _delete_document_artifacts(db, doc, vector_store, graph_builder, blob_store)
            doc.status = ProcessingStatus.DELETED
            await db.commit()

        elif operation == "refresh_permissions":
            await _refresh_permissions(db, doc, permission_resolver)


async def _ingest_new_document(db, doc, graph_client, blob_store,
                                vector_store, graph_builder, permission_resolver):
    """Full ingestion pipeline: download → OCR → chunk → embed → RBAC → graph."""
    from src.processing.pipeline import DocumentProcessingPipeline
    from src.chunking.adaptive_chunker import AdaptiveSemanticChunker
    from src.embedding.azure_openai import AzureOpenAIEmbedder

    try:
        # 1. Download from SharePoint → Azure Blob staging
        doc.status = ProcessingStatus.DOWNLOADING
        doc.processing_started_at = datetime.now(timezone.utc)
        await db.commit()

        download_url = await graph_client.get_download_url(
            doc.drive_id, doc.drive_item_id
        )
        file_bytes = await graph_client.download_file(download_url)

        # Content hash for dedup (skip if identical to previous version)
        import hashlib
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        if doc.content_hash == content_hash:
            logger.info("ingestion.skip_unchanged", doc_id=str(doc.id))
            doc.status = ProcessingStatus.COMPLETED
            await db.commit()
            return

        doc.content_hash = content_hash
        blob_path = await blob_store.upload(
            f"staging/{doc.drive_id}/{doc.drive_item_id}/{doc.name}",
            file_bytes,
        )
        doc.blob_path = blob_path

        # 2. OCR + Parsing + Metadata extraction
        doc.status = ProcessingStatus.PROCESSING
        await db.commit()

        pipeline = DocumentProcessingPipeline()
        processed = await pipeline.process(file_bytes, doc.name, doc.file_type)

        doc.summary = processed.summary
        doc.department = processed.department
        doc.content_type_tag = processed.content_type
        doc.topics = processed.topics
        doc.language = processed.language

        # 3. Chunking
        doc.status = ProcessingStatus.CHUNKING
        await db.commit()

        chunker = AdaptiveSemanticChunker()
        chunks = chunker.chunk(
            processed.text,
            file_type=doc.file_type,
            metadata={
                "document_id": str(doc.id),
                "document_title": doc.name,
                "sharepoint_url": doc.sharepoint_url,
                "department": doc.department,
                "summary_prefix": doc.summary,
            },
        )

        # 4. Embedding
        doc.status = ProcessingStatus.EMBEDDING
        await db.commit()

        embedder = AzureOpenAIEmbedder()
        chunk_texts = [c.text for c in chunks]
        embeddings = await embedder.embed_batch(chunk_texts)

        # 5. Index — write to pgvector + Neo4j
        doc.status = ProcessingStatus.INDEXING
        await db.commit()

        await vector_store.upsert_chunks(doc, chunks, embeddings)
        await graph_builder.index_document(doc, chunks, processed.entities)

        # 6. RBAC — resolve permissions
        await permission_resolver.resolve_and_store(doc)

        # 7. Done
        doc.status = ProcessingStatus.COMPLETED
        doc.processing_completed_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("ingestion.completed", doc_id=str(doc.id), name=doc.name,
                     chunks=len(chunks))

    except Exception as e:
        doc.status = ProcessingStatus.FAILED
        doc.error_message = str(e)[:1000]
        doc.retry_count += 1
        await db.commit()
        raise


async def _delete_document_artifacts(db, doc, vector_store, graph_builder, blob_store):
    """Remove all indexed artifacts for a document."""
    # Delete chunks + embeddings from pgvector
    await vector_store.delete_by_document(str(doc.id))

    # Delete nodes/relationships from Neo4j
    await graph_builder.delete_document(str(doc.id))

    # Delete staged blob
    if doc.blob_path:
        await blob_store.delete(doc.blob_path)

    # Delete permission records (cascade handles this via relationship)
    logger.info("ingestion.artifacts_deleted", doc_id=str(doc.id), name=doc.name)


async def _refresh_permissions(db, doc, permission_resolver):
    """Re-fetch permissions without re-processing content."""
    await permission_resolver.resolve_and_store(doc)

    # Invalidate RBAC cache for affected users
    from src.storage.cache import invalidate_rbac_cache_for_document
    await invalidate_rbac_cache_for_document(str(doc.id))

    logger.info("ingestion.permissions_refreshed", doc_id=str(doc.id))
```

---

### 13.6 Webhook Endpoint (Push Notifications)

```python
# src/api/routes/webhooks.py

import structlog
from fastapi import APIRouter, Request, Response
from src.celery_app import run_delta_sync

logger = structlog.get_logger()
router = APIRouter()


@router.post("/webhooks/graph/notify")
async def graph_webhook(request: Request):
    """
    Microsoft Graph webhook notification endpoint.

    Two modes:
    1. Validation: Graph sends a validationToken query param on subscription creation.
       We must echo it back as plain text within 10 seconds.
    2. Notification: Graph POSTs a JSON body with changed resources.
       We ACK immediately (202) and dispatch a Celery task to process.

    Webhook payload tells us WHICH drive changed, but not WHAT changed.
    We use delta query (via Celery task) to discover the actual changes.
    """
    # --- Subscription Validation ---
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        logger.info("webhook.validation", token=validation_token[:20] + "...")
        return Response(content=validation_token, media_type="text/plain")

    # --- Change Notification ---
    body = await request.json()
    notifications = body.get("value", [])

    for notification in notifications:
        resource = notification.get("resource", "")
        # resource looks like: "drives/{drive-id}/root"
        # Extract drive_id
        parts = resource.split("/")
        if len(parts) >= 2 and parts[0] == "drives":
            drive_id = parts[1]
            site_id = notification.get("tenantId", "")  # Or extract from clientState

            logger.info("webhook.notification",
                        drive_id=drive_id,
                        change_type=notification.get("changeType"))

            # Dispatch delta sync for this drive
            # Debounce: if multiple notifications arrive within seconds,
            # Celery's task dedup (via unique task ID) prevents duplicate syncs
            run_delta_sync.apply_async(
                kwargs={"site_id": site_id, "drive_id": drive_id},
                queue="rag.ingestion",
                task_id=f"delta_sync_{drive_id}",  # Dedup key
            )

    # ACK immediately — Graph requires 202 within 10 seconds
    return Response(status_code=202)
```

---

### 13.7 Webhook Subscription Management

```python
# src/connectors/webhook_manager.py

import structlog
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.connectors.graph_client import GraphClientFactory
from src.models.document import SyncCursor

logger = structlog.get_logger()

# Max subscription lifetime for driveItem webhooks
MAX_SUBSCRIPTION_DAYS = 30


class WebhookManager:
    """
    Manages Microsoft Graph webhook subscriptions for SharePoint drives.

    Subscriptions must be renewed before they expire (max 30 days for driveItem).
    Celery Beat runs renewal every 7 days for safety margin.
    """

    def __init__(self, graph_client, db: AsyncSession):
        self.graph = graph_client
        self.db = db

    async def ensure_subscriptions(self, drives: list[dict]):
        """
        Create or renew webhook subscriptions for all configured drives.

        Args:
            drives: List of {"site_id": ..., "drive_id": ...} dicts
        """
        for drive in drives:
            await self._ensure_subscription(drive["site_id"], drive["drive_id"])

    async def _ensure_subscription(self, site_id: str, drive_id: str):
        """Create a new subscription or renew an existing one."""
        resource = f"drives/{drive_id}/root"
        expiration = datetime.now(timezone.utc) + timedelta(days=MAX_SUBSCRIPTION_DAYS)

        # Check if we already have an active subscription
        # (stored as a field on SyncCursor or separate subscriptions table)
        existing_sub_id = await self._get_existing_subscription(drive_id)

        if existing_sub_id:
            # Renew
            try:
                await self.graph.patch(
                    f"/subscriptions/{existing_sub_id}",
                    json={
                        "expirationDateTime": expiration.isoformat() + "Z",
                    },
                )
                logger.info("webhook.renewed", drive_id=drive_id, sub_id=existing_sub_id)
                return
            except Exception as e:
                logger.warn("webhook.renew_failed", drive_id=drive_id, error=str(e))
                # Fall through to create new

        # Create new subscription
        from config.settings import settings
        response = await self.graph.post(
            "/subscriptions",
            json={
                "changeType": "updated",
                "notificationUrl": f"{settings.PUBLIC_BASE_URL}/webhooks/graph/notify",
                "resource": resource,
                "expirationDateTime": expiration.isoformat() + "Z",
                "clientState": f"{site_id}:{drive_id}",  # Verification token
            },
        )
        sub_data = response.json()
        sub_id = sub_data.get("id")

        await self._store_subscription(drive_id, sub_id)
        logger.info("webhook.created", drive_id=drive_id, sub_id=sub_id)

    async def _get_existing_subscription(self, drive_id: str) -> str | None:
        """Look up existing subscription ID for a drive."""
        # Could be stored in SyncCursor or a dedicated subscriptions table
        # Simplified: check Graph API for our subscriptions
        response = await self.graph.get("/subscriptions")
        subs = response.json().get("value", [])
        for sub in subs:
            if f"drives/{drive_id}" in sub.get("resource", ""):
                return sub["id"]
        return None

    async def _store_subscription(self, drive_id: str, sub_id: str):
        """Persist subscription ID for future renewal."""
        # Store on SyncCursor or dedicated table
        pass
```

---

### 13.8 Celery Beat Schedule (Poller Triggers)

```python
# config/celery_config.py — Updated beat schedule for CRUD poller

from celery.schedules import crontab

beat_schedule = {
    # ── PRIMARY: Webhook-triggered delta sync (via webhook endpoint) ──
    # Handled by the webhook endpoint dispatching run_delta_sync tasks.
    # No beat entry needed — it's event-driven.

    # ── FALLBACK: Poll all drives every 15 minutes ──
    # Catches anything webhooks missed (e.g., network blip, dropped notification)
    "delta-sync-all-drives": {
        "task": "src.celery_app.run_delta_sync_all",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "rag.ingestion"},
    },

    # ── DAILY: Full reconciliation ──
    # Microsoft recommends a daily full check to ensure nothing was missed.
    # Runs at 2 AM to avoid peak hours.
    "daily-full-reconciliation": {
        "task": "src.celery_app.run_full_reconciliation",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "rag.ingestion"},
    },

    # ── WEEKLY: Webhook subscription renewal ──
    # Graph subscriptions expire after 30 days. Renew every 7 days for safety.
    "renew-webhook-subscriptions": {
        "task": "src.celery_app.renew_webhooks",
        "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
        "options": {"queue": "rag.ingestion"},
    },

    # ── EVERY 4 HOURS: Permission re-expansion ──
    # Re-expands Entra ID group → user memberships for RBAC
    "refresh-permissions": {
        "task": "src.celery_app.run_permission_refresh",
        "schedule": crontab(minute=0, hour="*/4"),
        "options": {"queue": "rag.ingestion"},
    },

    # ── EVERY 10 MIN: Retry failed documents ──
    # Re-queue documents stuck in FAILED status with retry_count < 3
    "retry-failed-documents": {
        "task": "src.celery_app.retry_failed_documents",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "rag.ingestion"},
    },

    # ── HOUSEKEEPING (from Section 11.12) ──
    "cleanup-expired-streams": {
        "task": "src.celery_app.cleanup_streams",
        "schedule": crontab(minute="*/10"),
        "options": {"queue": "rag.query"},
    },
    "cleanup-checkpoints": {
        "task": "src.celery_app.cleanup_checkpoints",
        "schedule": crontab(minute=0, hour="*/1"),
        "options": {"queue": "rag.query"},
    },
}
```

```python
# src/celery_app.py — Supporting tasks

@celery_app.task(queue="rag.ingestion")
def run_delta_sync(site_id: str, drive_id: str):
    """Delta sync a single drive (triggered by webhook or Beat)."""
    import asyncio
    asyncio.run(_delta_sync_drive(site_id, drive_id))


@celery_app.task(queue="rag.ingestion")
def run_delta_sync_all():
    """Delta sync all configured drives (Beat fallback every 15 min)."""
    import asyncio
    asyncio.run(_delta_sync_all_drives())


@celery_app.task(queue="rag.ingestion")
def run_full_reconciliation():
    """
    Daily full reconciliation: compare our DB against SharePoint reality.

    For each configured drive:
    1. Reset delta token (force full enumeration)
    2. Run delta sync (returns ALL items)
    3. Compare against our documents table
    4. Detect orphans (in our DB but not in SharePoint) → mark deleted
    """
    import asyncio
    asyncio.run(_full_reconciliation())


@celery_app.task(queue="rag.ingestion")
def retry_failed_documents():
    """Re-queue documents stuck in FAILED status with retry_count < 3."""
    import asyncio
    asyncio.run(_retry_failed())


async def _delta_sync_all_drives():
    from config.settings import settings
    async with get_db_session() as db:
        graph_client = await GraphClientFactory.create()
        manager = DeltaSyncManager(graph_client, db)
        handler = ChangeHandler(db, graph_client)

        for drive_config in settings.SHAREPOINT_DRIVES:
            try:
                changes = await manager.sync_drive(
                    drive_config["site_id"],
                    drive_config["drive_id"],
                )
                if changes:
                    await handler.process_changes(changes)
            except Exception as e:
                logger.error("delta_sync_all.drive_failed",
                             drive_id=drive_config["drive_id"], error=str(e))


async def _retry_failed():
    async with get_db_session() as db:
        result = await db.execute(
            select(Document).where(
                Document.status == ProcessingStatus.FAILED,
                Document.retry_count < 3,
            )
        )
        failed_docs = result.scalars().all()

        for doc in failed_docs:
            run_ingestion_task.apply_async(
                kwargs={
                    "document_id": str(doc.id),
                    "drive_id": doc.drive_id,
                    "drive_item_id": doc.drive_item_id,
                    "operation": "create",  # Re-try full ingestion
                },
                queue="rag.ingestion",
            )
            logger.info("retry_failed.requeued", doc_id=str(doc.id), name=doc.name,
                         retry=doc.retry_count)
```

---

### 13.9 Graph API Client Helpers

```python
# src/connectors/graph_client.py — Key methods used by the poller

import httpx
import msal
from config.settings import settings


class GraphClientFactory:
    """Create authenticated Microsoft Graph API clients using MSAL."""

    @staticmethod
    async def create() -> "GraphClient":
        app = msal.ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        access_token = result["access_token"]
        return GraphClient(access_token)


class GraphClient:
    """Thin async wrapper around Microsoft Graph API."""

    BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def get(self, url: str, params: dict = None) -> httpx.Response:
        """GET request. Handles both relative paths and full URLs (nextLink/deltaLink)."""
        if url.startswith("http"):
            full_url = url
        else:
            full_url = f"{self.BASE_URL}{url}"

        response = await self.client.get(full_url, params=params)

        # Handle throttling (429)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 30))
            import asyncio
            await asyncio.sleep(retry_after)
            return await self.get(url, params)

        response.raise_for_status()
        return response

    async def post(self, url: str, json: dict = None) -> httpx.Response:
        full_url = f"{self.BASE_URL}{url}" if not url.startswith("http") else url
        response = await self.client.post(full_url, json=json)
        response.raise_for_status()
        return response

    async def patch(self, url: str, json: dict = None) -> httpx.Response:
        full_url = f"{self.BASE_URL}{url}" if not url.startswith("http") else url
        response = await self.client.patch(full_url, json=json)
        response.raise_for_status()
        return response

    async def get_download_url(self, drive_id: str, item_id: str) -> str:
        """Get the @microsoft.graph.downloadUrl for a driveItem."""
        response = await self.get(
            f"/drives/{drive_id}/items/{item_id}",
            params={"$select": "@microsoft.graph.downloadUrl"},
        )
        data = response.json()
        return data["@microsoft.graph.downloadUrl"]

    async def download_file(self, download_url: str) -> bytes:
        """Download file bytes from a temporary download URL."""
        response = await self.client.get(download_url)
        response.raise_for_status()
        return response.content

    async def close(self):
        await self.client.aclose()
```

---

### 13.10 Configuration

```python
# config/settings.py — Additions for poller

class Settings(BaseSettings):
    # ... existing settings ...

    # SharePoint drives to monitor (list of site_id + drive_id pairs)
    # Configured via JSON env var or admin API
    SHAREPOINT_DRIVES: list[dict] = []
    # Example: [{"site_id": "contoso.sharepoint.com,abc123", "drive_id": "b!xyz789"}]

    # Poller settings
    DELTA_SYNC_INTERVAL_MINUTES: int = 15        # Fallback polling interval
    DELTA_TOKEN_MAX_AGE_DAYS: int = 25           # Force re-crawl before 30-day expiry
    INGESTION_MAX_RETRIES: int = 3               # Max retries for failed documents
    INGESTION_RETRY_DELAY_SECONDS: int = 60      # Base retry delay (exponential backoff)
    SUPPORTED_FILE_EXTENSIONS: list[str] = [".pdf", ".docx", ".pptx", ".xlsx"]

    # Webhook
    PUBLIC_BASE_URL: str = "https://rag.contoso.com"  # For webhook callback URL
    WEBHOOK_CLIENT_STATE: str = ""                     # Verification secret

    # Throttle protection
    GRAPH_MAX_RETRIES: int = 5
    GRAPH_RETRY_BASE_DELAY: int = 5                    # seconds
```

```env
# .env additions for poller
SHAREPOINT_DRIVES='[{"site_id":"contoso.sharepoint.com,abc-123,def-456","drive_id":"b!xyz789"},{"site_id":"contoso.sharepoint.com,abc-123,ghi-012","drive_id":"b!uvw345"}]'
DELTA_SYNC_INTERVAL_MINUTES=15
PUBLIC_BASE_URL=https://rag.contoso.com
```

---

### 13.11 Key Design Decisions (CRUD Poller)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Change detection | Webhook + Delta Query hybrid | Microsoft's recommended pattern. Webhooks = instant notification. Delta query = reliable change enumeration. Belt + suspenders. |
| Polling fallback | Every 15 min via Celery Beat | Catches dropped webhooks. Low cost since delta query only returns changes since last token. |
| Full reconciliation | Daily at 2 AM | Catches edge cases (expired tokens, missed deletes). Microsoft recommends max once/day. |
| Change classification | cTag vs eTag comparison | cTag = content hash (changes on file edit). eTag = entity tag (changes on any update including permissions). cTag unchanged + eTag changed = permission-only change. |
| Content dedup | SHA-256 of downloaded bytes | Prevents re-processing when delta reports a change but the actual file content is identical (e.g., metadata-only update from SharePoint). |
| Delete strategy | Soft delete (tombstone) | Set status=DELETED but keep the Document row for audit trail. Hard-delete artifacts (chunks, embeddings, graph nodes, blob). |
| Update strategy | Delete-then-recreate (not patch) | Simpler than diffing chunks. A document update could change anything (structure, content, page count). Full re-ingestion ensures consistency. |
| Permission change | Lightweight refresh (no re-ingestion) | eTag-only changes don't need OCR/chunking/embedding. Just re-fetch permissions from Graph API and update RBAC tables. |
| Retry logic | Exponential backoff, max 3 retries, Beat re-queues | Transient failures (API throttling, network) auto-resolve. Persistent failures stay in FAILED for manual review. |
| Webhook dedup | Celery task_id = `delta_sync_{drive_id}` | Multiple webhook notifications for the same drive within seconds collapse into a single delta sync task. |
| Token expiry | Force re-crawl at 25 days (before 30-day expiry) | Graph delta tokens expire after ~30 days. Proactive re-crawl prevents HTTP 410 Gone errors. |
| Audit trail | SyncEvent table with raw delta JSON | Every detected change is logged with the original Graph API response. Enables replay if processing logic changes. |

---

### 13.12 Error Handling Matrix

| Error | Source | Handling |
|-------|--------|----------|
| HTTP 429 (Throttled) | Graph API | Respect `Retry-After` header, exponential backoff, pause all requests |
| HTTP 410 (Gone) | Delta API (token expired) | Clear delta_token, force full re-crawl on next sync |
| HTTP 401/403 | Graph API (auth) | Refresh MSAL token, retry once. If persistent, alert — app registration may need consent refresh. |
| HTTP 404 | Delta API (drive deleted) | Log warning, disable sync for this drive, alert admin. |
| HTTP 503 | Graph API (service unavail) | Retry with backoff. Celery's `acks_late` + `retry` handles this. |
| Processing failure | OCR/chunking/embedding | Document set to FAILED, retry_count incremented, Beat retries every 10 min (up to 3x). |
| Duplicate webhook | Graph sends duplicates | Celery task_id dedup + delta query is idempotent (same changes returned). |
| Large delta page | >1000 items in response | Pagination via @odata.nextLink handles automatically. Memory-efficient (process per page). |
| Network partition | Worker can't reach Graph | Task fails, Celery retries. `acks_late=True` ensures task isn't lost. |
