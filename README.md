# BetterRAG

Enterprise-grade agentic RAG system for SharePoint/OneDrive document intelligence. Ingests documents with full RBAC enforcement, retrieves with hybrid vector + graph search, and answers via a LangGraph multi-agent pipeline with department-aware routing.

---

## Architecture

```
SharePoint / OneDrive (Microsoft Graph API)
        │
    Source Connector ── RBAC from Entra ID
        │
    Azure Blob Storage (staging)
        │
    Document Processing
        ├── File parsers (pymupdf, python-pptx, openpyxl, python-docx)
        ├── Vision extraction (GPT-4.1-mini) — slides & PDF pages → PNG
        ├── Azure Document Intelligence (OCR fallback)
        └── LLM metadata + summarization [concurrent via asyncio.gather]
        │
    Adaptive Semantic Chunker
        │
    Azure OpenAI Embeddings (text-embedding-3-large, 1536d)
        │
    ┌───┴───┐
    │       │
 pgvector  Neo4j
 (HNSW)   (knowledge graph)
    │       │
    └───┬───┘
        │
    Retrieval Pipeline
        ├── HyDE (hypothetical document embedding)
        ├── Hybrid search (vector + PostgreSQL FTS, RRF fusion)
        ├── Graph expansion (cross-document relationships)
        └── Cohere reranking
        │
    LangGraph Orchestrator
        │
    ┌───┼───┬───┬───┐
   HR  Fin Sales Mkt General   (department sub-agents)
        │
    Document Generation
        └── PPTX / DOCX / XLSX from department templates
```

---

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + SSE streaming |
| Orchestration | LangGraph StateGraph |
| LLM | Azure OpenAI (gpt-4.1-mini), Anthropic Claude (fallback) |
| Embeddings | Azure OpenAI text-embedding-3-large |
| Vector store | PostgreSQL + pgvector (HNSW index) |
| Knowledge graph | Neo4j |
| Reranker | Cohere rerank-v4.0-fast (Azure AI Foundry) |
| Cache | Redis (query analysis, HyDE, RBAC, session) |
| Task queue | Celery + Redis Streams |
| Frontend | Open WebUI (Pipe + Action Function) |
| Infra | Kubernetes (AKS), Kustomize |

---

## Features

- **Hybrid retrieval** — vector cosine, MMR, HyDE cosine, RRF-fused with PostgreSQL full-text search
- **Knowledge graph expansion** — Neo4j relationships surface related documents beyond keyword match
- **Department routing** — HR, Finance, Sales, Marketing, General sub-agents with specialized prompts
- **RBAC enforcement** — Entra ID permissions propagated from SharePoint to every chunk
- **Document generation** — PPTX (python-pptx + PptxGenJS), DOCX, XLSX from department templates
- **Multi-turn conversations** — last 10 turns in LLM context, query reformulation for follow-ups
- **Open WebUI integration** — Pipe, Action Function (export buttons), Filter, DocGen Pipe

---

## Project Structure

```
better-rag/
├── config/settings.py              # All config via env vars / k8s secrets
├── src/
│   ├── main.py                     # FastAPI entry point
│   ├── graph/
│   │   ├── orchestrator.py         # LangGraph StateGraph — main pipeline
│   │   └── nodes/                  # query_analysis, smart_router, retrieval,
│   │                               # answer_generation, quality_check, doc_generation
│   ├── retrieval/
│   │   ├── pipeline.py             # Unified retrieval (hybrid + graph + rerank)
│   │   ├── query_analyzer.py       # Intent classification, strategy selection
│   │   ├── hyde.py                 # Hypothetical document embedding
│   │   └── reranker.py             # Cohere reranker wrapper
│   ├── agents/
│   │   ├── department_agent.py     # Department sub-agents
│   │   └── tools/                  # search_tool, doc_gen_tool, chart_tool
│   ├── document_generation/        # PPTX / DOCX / XLSX generators + spec builder
│   ├── openwebui/                  # pipe.py, action_function.py, docgen_pipe.py,
│   │                               # filter_function.py, pptxgenjs_pipe.py
│   ├── connectors/                 # SharePoint, Graph API, permissions
│   ├── processing/                 # OCR, chunking, embedding pipeline
│   ├── storage/                    # pgvector, Neo4j, Redis, Blob
│   └── api/routes/                 # chat, docgen, health, files
├── infra/                          # Kustomize manifests, AKS config
└── tests/
```

---

## Getting Started

```bash
cp .env.example .env
# Fill in Azure OpenAI, PostgreSQL, Neo4j, Redis, SharePoint credentials

docker-compose up -d
```

Run migrations:
```bash
alembic upgrade head
```

Trigger an initial SharePoint sync:
```bash
python -m src.cli sync --full
```

---

## Open WebUI Setup

Deploy each file in `src/openwebui/` as a Function in the OWUI admin panel:

| File | Type | Purpose |
|---|---|---|
| `pipe.py` | Pipe | Main RAG agent — routes all chat through BetterRAG backend |
| `docgen_pipe.py` | Pipe | Standalone document generation (file attachment context) |
| `pptxgenjs_pipe.py` | Pipe | Creative PPTX with native Office charts |
| `action_function.py` | Action | Export as PPTX / DOCX / XLSX buttons on assistant messages |
| `filter_function.py` | Filter | Department hint injection, PII scrubbing, query preprocessing |

Set Valves in each function to point `BETTER_RAG_API_URL` at your backend.

---

## Roadmap

### Phase 1 — Instrumentation
Add per-node execution timing (`NodeTimer`), end-to-end `duration_ms` in SSE metadata events, and structured log labels (`query_source`, `rag_triggered`, `skip_reason`). No behavior change — pure observability prerequisite for all optimization work.

### Phase 2 — Agentic Intent Router
Expand query classification beyond `smalltalk`. Four new types that skip RAG retrieval entirely:

| Type | Example | Action |
|---|---|---|
| `conversational` | "Explain that again" | Answer from conversation history |
| `context_followup` | "What about the HR version?" | Reuse cached chunks from session |
| `knowledge_only` | "What is VLOOKUP?" | Direct LLM answer, no KB needed |
| `computation` | "Summarize as bullet points" | Transform previous output |

Target: ≥30% of turns in multi-turn sessions skip retrieval.

### Phase 3 — Retrieval Latency
In order of expected impact:
1. Cap Cohere rerank input at 20 docs (reduces rerank latency ~40%)
2. Skip Neo4j graph expansion for `factual` queries (~100–200ms saved)
3. Skip HyDE for well-formed factual queries (one fewer LLM call)
4. Parallelize query analysis LLM call and raw query embedding

Target: P50 time-to-first-token ≤1.2s for RAG queries (from ~1.8–2.5s).

### Phase 4 — Multi-Turn Session Store
Redis-backed `ConversationSession` keyed by `session_id` (derived from OWUI `chat_id`):
- Persists last N turns + retrieved chunk cache
- Progressive summarization after turn 10 (compress old turns, bound token count)
- Session-aware follow-up detection reuses cached chunks at zero retrieval cost
- Token budget management replaces hard-coded `[-10]` slice

### Phase 5 — Doc Gen Graph Fix
Fix the orchestrator graph so retrieval runs **before** doc generation (currently the graph short-circuits to doc gen before retrieval, forcing a redundant re-fetch inside the node). Generative queries go through the full retrieval path; `doc_generation_node` receives pre-populated `reranked_results` from state.

### Phase 6 — Open WebUI Web Search Integration
OWUI's native web search injects results as a system message block into the messages array. The pipe parses and strips this block, normalizes results to chunk shape, and forwards them as `web_context` in the request body. The orchestrator merges enterprise chunks (`[1]`, `[2]`) and web results (`[W1]`, `[W2]`) into a single context block with distinct citation labels. Image URLs extracted from web context are injected into PPTX/DOCX via `python-pptx`'s `add_picture()` API.

---

## Agentic Tool Loop

### Overview

The answer generation phase is evolving from a single LLM call to a **tool-use loop** backed by Azure OpenAI native function calling. After initial RAG retrieval, the LLM receives a toolbox and decides which tools to invoke based on the request. The loop runs up to 5 iterations; every tool execution emits a visible status event to the user.

```
retrieval_node
      │
agentic_answer_node
      │
      ├── LLM call (with tool definitions)
      │       │
      │   tool_calls? ──── yes ──→ execute tools (parallel) ──→ re-submit results
      │       │
      │      no
      │       │
      └── final answer → quality_check
```

Tools are selected at the LLM's discretion. Nothing is forced — the LLM calls a tool only when the request warrants it.

---

### Tools

#### `search_knowledge_base`
Targeted semantic search against the enterprise KB, beyond the initial RAG retrieval pass. Enables multi-hop reasoning and cross-department lookups.

- **Infrastructure**: [`src/agents/tools/search_tool.py`](src/agents/tools/search_tool.py) — already implemented, RBAC-filtered
- **Effort**: XS — register existing tool in the registry
- **Status**: Ready

#### `generate_document`
Generate a PPTX, DOCX, or XLSX from retrieved content and user instructions. The LLM builds the document spec; the generator renders it.

- **Infrastructure**: [`src/document_generation/generator_factory.py`](src/document_generation/generator_factory.py), [`spec_builder.py`](src/document_generation/spec_builder.py) — full pipeline exists
- **Effort**: S — wrap as tool function; emit `file` SSE event after execution
- **Status**: Ready (pending Phase 5 graph fix)

#### `edit_document`
Modify a previously generated document — add/remove slides, change content, adjust structure. Uses the existing spec sidecar pattern.

- **Infrastructure**: [`generator_factory.py`](src/document_generation/generator_factory.py) already supports `previous_filename` + `edit_request` edit mode
- **Effort**: S — expose edit mode as a tool; requires session `generated_file` from Phase 4/5
- **Status**: Ready after Phase 4 + 5

#### `generate_chart`
Produce a chart image (bar, line, pie, donut, waterfall, heatmap) from numeric data found in retrieved content.

- **Infrastructure**: [`src/agents/tools/chart_tool.py`](src/agents/tools/chart_tool.py), [`src/document_generation/chart_builder.py`](src/document_generation/chart_builder.py) — fully built
- **Effort**: XS — already a `@tool`; register in registry
- **Status**: Ready

#### `summarize_content`
Compress one or more retrieved documents into a concise summary at a specified detail level.

- **Infrastructure**: LLM call only
- **Effort**: XS
- **Status**: New, trivial

#### `extract_structured_data`
Extract tables, key-value pairs, or lists from retrieved chunks as structured JSON. Primary feeder for `generate_chart` and XLSX generation.

- **Infrastructure**: LLM + JSON mode
- **Effort**: XS
- **Status**: New, trivial

#### `translate_content`
Translate the agent's answer or retrieved content into another language (Arabic, French, etc.). Azure OpenAI handles translation natively.

- **Infrastructure**: Azure OpenAI — no additional API
- **Effort**: XS
- **Status**: New, trivial

#### `compare_documents`
Side-by-side structured comparison of two or more documents or policies, returned as a markdown table.

- **Infrastructure**: Extend `search_tool.py` with `document_title_filter`; LLM comparison prompt
- **Effort**: S
- **Status**: New

#### `draft_communication`
Draft a professional email, internal announcement, or Teams message using retrieved content as the factual basis. Produces a draft only — nothing is sent.

- **Infrastructure**: LLM call with structured output (to / subject / body)
- **Effort**: S
- **Status**: New

---

### Implementation Plan

#### Week 8 — Core tool loop + Tier 1 tools
- [ ] `src/agents/tool_registry.py` — `ToolRegistry` class, `build_tool_registry(state)` factory, OpenAI schema generation from type hints + docstrings
- [ ] `src/graph/nodes/agentic_answer.py` — `agentic_answer_node` with tool-use loop (max 5 iterations), parallel tool execution via `asyncio.gather`, status SSE events per tool call
- [ ] Wire `agentic_answer_node` into `src/graph/orchestrator.py` replacing `answer_generation_node`
- [ ] Register Tier 1 tools: `search_knowledge_base`, `generate_document`, `generate_chart`, `summarize_content`, `extract_structured_data`
- [ ] Add `tools_called: list[str]` to `src/models/state.py`

#### Week 9 — Tier 2 tools
- [ ] `src/agents/tools/summarize_tool.py` — `summarize_content`
- [ ] `src/agents/tools/translate_tool.py` — `translate_content`
- [ ] `src/agents/tools/extract_tool.py` — `extract_structured_data`
- [ ] `src/agents/tools/compare_tool.py` — `compare_documents` (extend `search_tool.py` with `document_title_filter`)
- [ ] `src/agents/tools/draft_tool.py` — `draft_communication`
- [ ] `src/agents/tools/doc_gen_tool.py` — `edit_document` (expose generator edit mode as tool)

#### Future — M365 Graph tools (de-prioritized)
These require additional Microsoft Graph API scopes and in some cases a delegated OAuth flow. Implement after Tier 1 + 2 are stable.

- `get_document_metadata` — live SharePoint metadata (last modified, author, version). Requires `Files.Read.All` (already provisioned for ingestion). Effort: M.
- `lookup_employee_profile` — Microsoft Graph People API. Requires `People.Read.All` scope. Effort: M.
- `create_calendar_event` — O365 calendar write via delegated auth. Requires `Calendars.ReadWrite` delegated scope + user OAuth token from OWUI. Must show event preview before creating. Effort: L.

---

### Tool Registry Design

```python
# src/agents/tool_registry.py

class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Callable] = {}
        self.definitions: list[dict] = []

    def register(self, fn: Callable, enabled: bool = True) -> None:
        """Register a tool. Schema is generated from type hints + docstring."""
        if enabled:
            schema = _fn_to_openai_schema(fn)
            self.tools[schema["function"]["name"]] = fn
            self.definitions.append(schema)

    async def execute(self, name: str, arguments: dict) -> str:
        fn = self.tools.get(name)
        if not fn:
            return f"Unknown tool: {name}"
        result = await fn(**arguments)
        return json.dumps(result) if isinstance(result, dict) else str(result)


def build_tool_registry(state: AgentState) -> ToolRegistry:
    registry = ToolRegistry()
    settings = get_settings()

    # Always available
    registry.register(search_knowledge_base)
    registry.register(summarize_content)
    registry.register(translate_content)
    registry.register(extract_structured_data)
    registry.register(draft_communication)
    registry.register(compare_documents)

    # Available when generation infrastructure is configured
    registry.register(generate_document)
    registry.register(generate_chart)

    # Available when a document was already generated this session
    registry.register(
        edit_document,
        enabled=bool(state.get("generated_file"))
    )

    # M365 tools — only when Graph credentials are present (de-prioritized)
    if settings.GRAPH_CLIENT_ID:
        registry.register(get_document_metadata)
        registry.register(lookup_employee_profile)

    return registry
```

---

### Guiding Principles for Tool Use

- **LLM decides when to call tools** — the agent is not pre-programmed to call a tool for a given query type. It reads the context and decides.
- **No forced invocations** — tools are offered to the LLM as options, never triggered by the orchestrator directly (except `generate_document` in the existing deterministic path, which is being replaced by Phase 5 + 7).
- **Tools are transparent to the user** — every tool call emits a `status` SSE event before execution so the user sees what the agent is doing.
- **Tool calls are additive** — a tool result is appended to the message history and re-submitted to the LLM. The LLM reasons over the result and may call more tools or produce the final answer.
- **Guard rails** — max 5 iterations enforced by `iteration_count` in `AgentState`. If the limit is hit, the LLM is forced to answer with whatever context it has.
