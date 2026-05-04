"""
Standalone FastAPI server for local OpenWebUI testing.

Wraps the same retrieval pipeline as test_query.py and exposes it as a
streaming SSE endpoint. No Celery, no Redis — connects directly to Postgres
and Azure OpenAI just like test_query.py does.

Usage:
    python scripts/openwebui_server.py

    Server runs on http://localhost:8000

    In OpenWebUI → Admin → Functions → BetterRAG pipe valves:
        BETTER_RAG_API_URL = http://host.docker.internal:8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="BetterRAG Local Test Server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
    return ""


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _rag_event_generator(query: str, top_k: int = 5):
    """
    Core RAG pipeline — mirrors test_query.py exactly:
      embed → pgvector search → cohere rerank → neo4j graph → stream LLM answer → citations
    """
    from sqlalchemy import text
    from openai import AzureOpenAI

    from config.settings import get_settings
    from src.embedding.azure_openai import AzureOpenAIEmbedder
    from src.storage.db import get_db_session
    from src.retrieval.reranker import CohereReranker
    from src.storage.vector_store import ChunkResult
    from src.knowledge_graph.builder import GraphBuilder

    settings = get_settings()

    try:
        # ── 1. Embed query ────────────────────────────────────────────────────
        yield _sse({"type": "status", "message": "Embedding query…", "done": False})
        embedder = AzureOpenAIEmbedder()
        query_embedding = await embedder.embed_query(query)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        # ── 2. Vector search ──────────────────────────────────────────────────
        fetch_k = top_k * 4
        yield _sse({"type": "status", "message": f"Searching top {fetch_k} candidates…", "done": False})

        async with get_db_session() as db:
            await db.execute(text(f"SET hnsw.ef_search = {settings.PGVECTOR_HNSW_EF_SEARCH}"))
            rows = await db.execute(
                text("""
                    SELECT
                        dc.id, dc.document_id, dc.content, dc.content_with_context,
                        dc.section_heading, dc.page_numbers, dc.department,
                        dc.document_title, dc.sharepoint_url,
                        1 - (dc.embedding <=> cast(:embedding as vector)) AS score,
                        d.file_type, d.summary, d.content_type_tag
                    FROM document_chunks dc
                    JOIN documents d ON dc.document_id = d.id
                    WHERE d.status = 'completed'
                    ORDER BY dc.embedding <=> cast(:embedding as vector)
                    LIMIT :k
                """),
                {"embedding": embedding_str, "k": fetch_k},
            )
            candidates = [
                {
                    "chunk_id": str(row[0]),
                    "document_id": str(row[1]),
                    "content": row[2],
                    "content_with_context": row[3],
                    "section_heading": row[4],
                    "page_numbers": row[5],
                    "department": row[6],
                    "document_title": row[7],
                    "source": row[8],
                    "score": round(float(row[9]), 4),
                    "file_type": row[10],
                    "summary": row[11],
                    "content_type_tag": row[12],
                }
                for row in rows.fetchall()
            ]

        if not candidates:
            yield _sse({"type": "error", "message": "No results found. Make sure documents have been ingested."})
            return

        # ── 3. Rerank ─────────────────────────────────────────────────────────
        yield _sse({"type": "status", "message": f"Reranking to top {top_k}…", "done": False})
        try:
            chunk_results = [
                ChunkResult(
                    chunk_id=c["chunk_id"],
                    document_id=c["document_id"],
                    content=c["content"],
                    content_with_context=c["content_with_context"],
                    chunk_type="text",
                    sequence_number=0,
                    page_numbers=c["page_numbers"],
                    section_heading=c["section_heading"],
                    department=c["department"],
                    sharepoint_url=c["source"],
                    document_title=c["document_title"],
                    score=c["score"],
                )
                for c in candidates
            ]
            reranker = CohereReranker()
            reranked = await reranker.rerank(query=query, candidates=chunk_results, top_k=top_k)
            extra_by_chunk_id = {c["chunk_id"]: c for c in candidates}
            chunks = [
                {
                    "chunk_id": r.chunk.chunk_id,
                    "document_id": r.chunk.document_id,
                    "content": r.chunk.content,
                    "content_with_context": r.chunk.content_with_context,
                    "section_heading": r.chunk.section_heading,
                    "page_numbers": r.chunk.page_numbers,
                    "department": r.chunk.department,
                    "document_title": r.chunk.document_title,
                    "source": r.chunk.sharepoint_url,
                    "score": round(r.rerank_score, 4),
                    "file_type": extra_by_chunk_id.get(r.chunk.chunk_id, {}).get("file_type"),
                    "summary": extra_by_chunk_id.get(r.chunk.chunk_id, {}).get("summary"),
                    "content_type_tag": extra_by_chunk_id.get(r.chunk.chunk_id, {}).get("content_type_tag"),
                }
                for r in reranked
            ]
        except Exception:
            chunks = candidates[:top_k]

        # ── 4. Neo4j graph context ────────────────────────────────────────────
        yield _sse({"type": "status", "message": "Fetching graph context…", "done": False})
        graph_context: list[dict] = []
        graph_chunks: list[dict] = []
        try:
            doc_ids = list({c["document_id"] for c in chunks})
            builder = GraphBuilder()
            graph_context = await builder.expand_from_documents(doc_ids=doc_ids, limit=5)

            if graph_context:
                related_doc_ids = [ctx["doc_id"] for ctx in graph_context if ctx.get("doc_id")]
                new_doc_ids = [d for d in related_doc_ids if d not in set(doc_ids)]
                if new_doc_ids:
                    placeholders = ", ".join(f"'{d}'" for d in new_doc_ids)
                    async with get_db_session() as db:
                        rows = await db.execute(
                            text(f"""
                                SELECT chunk_id, document_id, content, content_with_context,
                                       section_heading, page_numbers, department, document_title,
                                       sharepoint_url, score
                                FROM (
                                    SELECT
                                        dc.id AS chunk_id, dc.document_id, dc.content,
                                        dc.content_with_context, dc.section_heading, dc.page_numbers,
                                        dc.department, dc.document_title, dc.sharepoint_url,
                                        1 - (dc.embedding <=> cast(:embedding as vector)) AS score,
                                        ROW_NUMBER() OVER (
                                            PARTITION BY dc.document_id
                                            ORDER BY dc.embedding <=> cast(:embedding as vector)
                                        ) AS rn
                                    FROM document_chunks dc
                                    JOIN documents d ON dc.document_id = d.id
                                    WHERE d.status = 'completed'
                                      AND dc.document_id::text IN ({placeholders})
                                ) ranked
                                WHERE rn <= 6
                                ORDER BY score DESC
                            """),
                            {"embedding": embedding_str},
                        )
                        graph_chunks = [
                            {
                                "chunk_id": str(row[0]),
                                "document_id": str(row[1]),
                                "content": row[2],
                                "content_with_context": row[3],
                                "section_heading": row[4],
                                "page_numbers": row[5],
                                "department": row[6],
                                "document_title": row[7],
                                "source": row[8],
                                "score": round(float(row[9]), 4),
                                "via_graph": True,
                            }
                            for row in rows.fetchall()
                        ]
        except Exception:
            pass  # Neo4j unavailable — continue without graph context

        all_chunks = chunks + graph_chunks

        # ── 5. Build context and stream LLM answer ────────────────────────────
        yield _sse({"type": "status", "message": "Generating answer…", "done": False})

        context_parts = []
        for i, chunk in enumerate(all_chunks, 1):
            title = chunk.get("document_title") or "Unknown"
            heading = chunk.get("section_heading") or ""
            header = f"[{i}] {title}" + (f" — {heading}" if heading else "")
            context_parts.append(f"{header}\n{chunk['content_with_context']}")

        if graph_context:
            related_parts = []
            for ctx in graph_context[:3]:
                title = ctx.get("title", "")
                summary = ctx.get("summary", "")
                rel_types = ctx.get("relationship_types") or []
                if title and summary:
                    rel_label = f" ({', '.join(rel_types)})" if rel_types else ""
                    related_parts.append(f"[Related{rel_label}] {title}\n{summary[:300]}")
            if related_parts:
                context_parts.append(
                    "--- Related Documents (via knowledge graph) ---\n" + "\n\n".join(related_parts)
                )

        context = "\n\n---\n\n".join(context_parts)
        system_prompt = (
            "You are a helpful enterprise assistant. Answer the user's question based only on the "
            "provided context. Cite sources by their [number]. If the context doesn't contain enough "
            "information, say so clearly."
        )

        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        stream = client.chat.completions.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
            max_completion_tokens=16384,
            stream=True,
        )
        for chunk_resp in stream:
            if chunk_resp.choices and chunk_resp.choices[0].delta.content:
                yield _sse({"type": "token", "content": chunk_resp.choices[0].delta.content})

        # ── 6. Emit citations ─────────────────────────────────────────────────
        seen: set[str] = set()
        for chunk in all_chunks:
            title = chunk.get("document_title") or "Unknown"
            if title not in seen:
                seen.add(title)
                # Build structured citation content from actual DB fields
                meta_lines = []
                if chunk.get("file_type"):
                    meta_lines.append(f"**Type:** {chunk['file_type'].upper()}")
                if chunk.get("department"):
                    meta_lines.append(f"**Department:** {chunk['department']}")
                if chunk.get("content_type_tag"):
                    meta_lines.append(f"**Content Type:** {chunk['content_type_tag']}")
                if chunk.get("section_heading"):
                    meta_lines.append(f"**Section:** {chunk['section_heading']}")
                if chunk.get("page_numbers"):
                    pages = ", ".join(str(p) for p in chunk["page_numbers"])
                    meta_lines.append(f"**Pages:** {pages}")

                # Use document summary if available, else chunk snippet
                body = chunk.get("summary") or chunk["content"][:500]

                content = ("\n".join(meta_lines) + "\n\n---\n\n" if meta_lines else "") + body

                yield _sse({
                    "type": "citation",
                    "title": title,
                    "content": content,
                    "sharepoint_url": chunk.get("source", ""),
                    "department": chunk.get("department", ""),
                    "section": chunk.get("section_heading", ""),
                    "file_type": chunk.get("file_type", ""),
                    "pages": chunk.get("page_numbers") or [],
                })

        yield _sse({"type": "status", "message": "Done", "done": True})

    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})

    finally:
        yield "data: [DONE]\n\n"


async def _direct_llm_sse_generator(messages: list[dict]):
    """Stream LLM response directly (no RAG) in the internal SSE token format."""
    from config.settings import get_settings
    from openai import AzureOpenAI

    settings = get_settings()
    client = AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )
    # Pass content through as-is — Azure OpenAI vision models accept
    # both plain strings and multimodal lists (text + image_url parts).
    normalized = [{"role": msg["role"], "content": msg.get("content", "")} for msg in messages]

    try:
        stream = client.chat.completions.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            messages=normalized,
            stream=True,
        )
        for resp in stream:
            delta = resp.choices[0].delta if resp.choices else None
            if delta and delta.content:
                yield _sse({"type": "token", "content": delta.content})
        yield _sse({"type": "status", "message": "Done", "done": True})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        yield "data: [DONE]\n\n"


@app.post("/api/v1/chat/completions")
@app.post("/api/v1/chat/stream")
async def chat(request: Request):
    """Internal SSE endpoint used by the custom pipe function."""
    body = await request.json()
    messages = body.get("messages", [])
    top_k: int = int(body.get("top_k", 5))

    query = _last_user_message(messages)
    if not query:
        async def _err():
            yield _sse({"type": "error", "message": "No user message found."})
            yield "data: [DONE]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    if _has_file_content(messages):
        return StreamingResponse(
            _direct_llm_sse_generator(messages),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _rag_event_generator(query, top_k=top_k),
        media_type="text/event-stream",
    )


@app.post("/v1/files")
async def upload_file(file: UploadFile = File(...), purpose: str = Form("assistants")):
    """
    OpenAI-compatible file upload endpoint.
    OpenWebUI calls this when you attach a file in the chat.
    The file content is extracted by OpenWebUI and sent in the messages — we just
    acknowledge the upload here so OpenWebUI doesn't error out.
    """
    import time
    import uuid

    file_id = f"file-{uuid.uuid4().hex[:12]}"
    return {
        "id": file_id,
        "object": "file",
        "filename": file.filename,
        "purpose": purpose,
        "status": "processed",
        "created_at": int(time.time()),
    }


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible model list — lets OpenWebUI discover the model."""
    return {
        "object": "list",
        "data": [
            {
                "id": "better-rag",
                "object": "model",
                "created": 1700000000,
                "owned_by": "better-rag",
            }
        ],
    }


def _has_file_content(messages: list[dict]) -> bool:
    """
    Detect if OpenWebUI has injected file content into the messages.
    When a user uploads a file, OpenWebUI extracts its text and either:
    - adds a system message with the file content, or
    - makes the user message content a list (multimodal format)
    We treat total message text > 2000 chars as a signal that file content is present.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            return True  # multimodal format — definitely has file content
        total += len(content)
    return total > 2000


@app.post("/v1/chat/completions")
async def openai_chat(request: Request):
    """
    OpenAI-compatible streaming endpoint.

    Add this server as an OpenAI connection in OpenWebUI:
      Admin Panel → Settings → Connections → OpenAI API
      URL: http://host.docker.internal:8000
      Key: any non-empty string (e.g. "local")

    Routing:
    - File uploaded in chat → pass messages directly to LLM (no RAG)
    - Normal text query   → run full RAG pipeline
    """
    import time
    import uuid

    from config.settings import get_settings
    from openai import AzureOpenAI

    body = await request.json()
    messages = body.get("messages", [])
    top_k: int = int(body.get("top_k", 5))

    query = _last_user_message(messages)
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    async def openai_stream():
        if not query:
            chunk = {
                "id": chat_id, "object": "chat.completion.chunk",
                "created": created, "model": "better-rag",
                "choices": [{"index": 0, "delta": {"content": "No message provided."}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return

        if _has_file_content(messages):
            # ── File mode: pass messages directly to LLM, skip RAG ──────────
            settings = get_settings()
            client = AzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            # Pass content as-is — Azure OpenAI vision models accept
            # both plain strings and multimodal lists (text + image_url parts).
            normalized = [{"role": msg["role"], "content": msg.get("content", "")} for msg in messages]

            stream = client.chat.completions.create(
                model=settings.LLM_EXPENSIVE_MODEL,
                messages=normalized,
                stream=True,
            )
            for resp in stream:
                delta = resp.choices[0].delta if resp.choices else None
                if delta and delta.content:
                    chunk = {
                        "id": chat_id, "object": "chat.completion.chunk",
                        "created": created, "model": "better-rag",
                        "choices": [{"index": 0, "delta": {"content": delta.content}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
        else:
            # ── RAG mode: vector search → rerank → graph → LLM ──────────────
            async for sse_line in _rag_event_generator(query, top_k=top_k):
                if not sse_line.startswith("data: "):
                    continue
                raw = sse_line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "token":
                    chunk = {
                        "id": chat_id, "object": "chat.completion.chunk",
                        "created": created, "model": "better-rag",
                        "choices": [{"index": 0, "delta": {"content": event.get("content", "")}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

        # Signal end
        done_chunk = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": "better-rag",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(done_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(openai_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

