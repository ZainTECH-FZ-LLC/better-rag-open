"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator

from config.settings import get_settings
from src.api.middleware.auth import AuthMiddleware
from src.api.sse import done_event, error_event, token_event
from src.storage.db import close_db, init_db

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    logger.info("app.starting", app_name=settings.APP_NAME)
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    async def _neo4j_init():
        try:
            from src.knowledge_graph.builder import init_neo4j_schema
            await init_neo4j_schema()
        except Exception as exc:
            logger.warning("app.neo4j_init_failed", error=str(exc))

    asyncio.create_task(_neo4j_init())

    yield

    await close_db()
    try:
        from src.knowledge_graph.builder import close_neo4j_driver
        await close_neo4j_driver()
    except Exception:
        pass
    logger.info("app.shutdown")


app = FastAPI(
    title="BetterRAG API",
    version="0.1.0",
    description="Enterprise Agentic RAG with SharePoint integration and RBAC",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

# ── Prometheus metrics ─────────────────────────────────────────────────────────

Instrumentator().instrument(app).expose(app, include_in_schema=False)

# ── Routers ────────────────────────────────────────────────────────────────────

from src.api.routes.chat import router as chat_router
from src.api.routes.customer_care import router as cc_router
from src.api.routes.files import router as files_router
from src.api.routes.health import router as health_router
from src.api.routes.webhooks import router as webhooks_router

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(webhooks_router)
app.include_router(cc_router)


# ── Local streaming endpoint (no Celery/Redis required) ───────────────────────
# Used by the Open WebUI Pipe Function during local development.
# Production uses /api/v1/chat/completions via the Celery/Redis Streams path
# in src/api/routes/chat.py (registered above via chat_router).

@app.post("/api/v1/chat/stream")
async def chat_completions(request: Request):
    """
    Main chat endpoint — OpenAI-compatible request format, BetterRAG SSE events.

    Receives an OpenAI-style messages array, runs the LangGraph orchestrator,
    and streams typed SSE events:
      { type: "token",    content: "..." }
      { type: "citation", title: "...", sharepoint_url: "...", ... }
      { type: "file",     filename: "...", download_url: "...", ... }
      { type: "status",   message: "...", step: "...", done: bool }
      { type: "metadata", department: "...", retrieval_strategy: "...", ... }
      { type: "error",    message: "..." }
      [DONE]

    For Celery/Redis Streams scaled path, see src/api/routes/chat.py.
    """
    body = await request.json()
    user_id = getattr(request.state, "user_id", "anonymous")
    user_email = getattr(request.state, "user_email", "")
    messages = body.get("messages", [])
    stream = body.get("stream", True)
    document_type = body.get("document_type")
    retrieval_strategy_override = body.get("retrieval_strategy_override")

    async def event_generator():
        try:
            from src.graph.orchestrator import run_orchestrator
            async for event in run_orchestrator(
                messages=messages,
                user_id=user_id,
                user_email=user_email,
                document_type=document_type,
                retrieval_strategy_override=retrieval_strategy_override,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("chat_completions.error", error=str(exc), user_id=user_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    if not stream:
        # Non-streaming: collect all events and return a single JSON response
        tokens = []
        citations = []
        documents = []
        metadata = {}

        async for line in event_generator():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "token":
                tokens.append(ev.get("content", ""))
            elif etype == "citation":
                citations.append(ev)
            elif etype == "file":
                documents.append(ev)
            elif etype == "metadata":
                metadata = ev

        from fastapi.responses import JSONResponse
        return JSONResponse({
            "answer": "".join(tokens),
            "citations": citations,
            "documents": documents,
            "metadata": metadata,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Direct retrieval endpoint (temporary) ─────────────────────────────────────
# Uses query_rag pipeline directly instead of the LangGraph orchestrator.
# Kept separate so the orchestrator path above is preserved for later use.
# Toggle via pipe_function.py valve: use_direct_retrieval = true

@app.post("/api/v1/chat/direct")
async def chat_direct(request: Request):
    """
    Direct retrieval endpoint — uses query_rag pipeline (embed → vector search →
    graph expand → rerank → LLM answer). Same SSE event format as /api/v1/chat/completions.
    """
    from src.retrieval.query import query_rag

    body = await request.json()
    # Use "anonymous" for RBAC — document_user_access is populated with "anonymous"
    # during ingestion. Switch to request.state.user_id once per-user RBAC is set up.
    user_id = "anonymous"
    messages = body.get("messages", [])

    query = next(
        (m["content"] for m in reversed(messages)
         if m.get("role") == "user" and isinstance(m.get("content"), str)),
        "",
    )

    async def event_generator():
        try:
            if not query:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No user message found.'})}\n\n"
                return

            status_msg = json.dumps({"type": "status", "message": "Searching knowledge base\u2026", "done": False})
            yield f"data: {status_msg}\n\n"
            result = await query_rag(query, user_id=user_id, top_k=10)

            if result.answer:
                yield f"data: {json.dumps({'type': 'token', 'content': result.answer})}\n\n"

            seen: set[str] = set()
            for chunk in result.chunk_dicts:
                title = chunk.get("document_title") or "Unknown"
                if title not in seen:
                    seen.add(title)
                    cit = next(
                        (c for c in result.citations if c.get("document_title") == title), {}
                    )
                    content = (chunk.get("content_with_context") or chunk.get("content", ""))[:500]
                    yield f"data: {json.dumps({'type': 'citation', 'title': title, 'content': content, 'sharepoint_url': chunk.get('source', ''), 'department': cit.get('department', ''), 'section': chunk.get('section_heading', ''), 'file_type': '', 'pages': cit.get('page_numbers') or []})}\n\n"

            yield f"data: {json.dumps({'type': 'status', 'message': 'Done', 'done': True})}\n\n"

        except Exception as exc:
            logger.error("chat_direct.error", error=str(exc), user_id=user_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Plain LLM endpoint (no retrieval, no system prompt) ───────────────────────
# Used by the Simple LLM Open WebUI Pipe Function.

@app.post("/api/v1/chat/llm")
async def chat_llm(request: Request):
    """
    Plain LLM endpoint — forwards messages directly to Azure OpenAI with no
    retrieval, no system prompt injection, and no RAG processing.

    Streams typed SSE events:
      { type: "token", content: "..." }
      { type: "error", message: "..." }
      [DONE]
    """
    from openai import AsyncAzureOpenAI

    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model") or settings.LLM_CHEAP_MODEL

    async def event_generator():
        try:
            client = AsyncAzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield f"data: {json.dumps({'type': 'token', 'content': delta.content})}\n\n"
        except Exception as exc:
            logger.error("chat_llm.error", error=str(exc))
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
