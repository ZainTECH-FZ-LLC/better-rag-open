"""Chat API routes — SSE streaming via Redis Streams bridge."""

from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from config.settings import get_settings
from src.celery_app import run_query_task

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """
    Scaled SSE chat endpoint via Redis Streams bridge.

    Flow:
    1. API pod receives request, generates stream_key
    2. Dispatches Celery task to query worker
    3. Query worker runs LangGraph, publishes events to Redis Stream
    4. API pod reads Redis Stream and forwards SSE events to client

    This allows any API pod to serve the SSE stream, regardless of which
    worker processes the query — critical for horizontal scaling on AKS.
    """
    body = await request.json()
    user_id = request.headers.get("X-User-Id", "anonymous")
    user_email = request.headers.get("X-User-Email", "")
    messages = body.get("messages", [])

    # Generate unique stream key
    stream_key = f"rag:stream:{uuid.uuid4().hex}"

    # Dispatch to Celery worker
    run_query_task.apply_async(
        kwargs={
            "messages": messages,
            "user_id": user_id,
            "user_email": user_email,
            "stream_key": stream_key,
        },
        queue="rag.query",
    )

    logger.info("chat.dispatched", stream_key=stream_key, user_id=user_id)

    # Stream from Redis
    async def event_generator():
        settings = get_settings()
        client = aioredis.from_url(settings.redis_streams_url)
        last_id = "0-0"

        try:
            while True:
                # Block-read from Redis Stream (5 second timeout)
                entries = await client.xread(
                    {stream_key: last_id},
                    count=100,
                    block=5000,
                )

                if not entries:
                    # Timeout — send keepalive comment
                    yield {"event": "ping", "data": ""}
                    continue

                for stream_name, messages_batch in entries:
                    for msg_id, fields in messages_batch:
                        last_id = msg_id
                        data = fields.get(b"data", b"").decode("utf-8")

                        if data == "[DONE]":
                            yield {"data": "[DONE]"}
                            return

                        yield {"data": data}

        except Exception as e:
            logger.error("chat.stream_error", error=str(e))
            yield {"data": json.dumps({"type": "error", "message": str(e)})}
            yield {"data": "[DONE]"}
        finally:
            # Clean up stream
            try:
                await client.delete(stream_key)
            except Exception:
                pass
            await client.aclose()

    return EventSourceResponse(event_generator())


@router.get("/chat/stream/{stream_key}")
async def resume_stream(stream_key: str, request: Request):
    """
    Resume an existing SSE stream (for reconnection).

    If a client disconnects and reconnects, it can resume from
    the last received message using Last-Event-ID header.
    """
    last_event_id = request.headers.get("Last-Event-ID", "0-0")

    async def event_generator():
        settings = get_settings()
        client = aioredis.from_url(settings.redis_streams_url)

        try:
            while True:
                entries = await client.xread(
                    {stream_key: last_event_id},
                    count=100,
                    block=5000,
                )

                if not entries:
                    yield {"event": "ping", "data": ""}
                    continue

                for stream_name, messages_batch in entries:
                    for msg_id, fields in messages_batch:
                        last_event_id_bytes = msg_id
                        data = fields.get(b"data", b"").decode("utf-8")

                        if data == "[DONE]":
                            yield {"id": msg_id.decode(), "data": "[DONE]"}
                            return

                        yield {
                            "id": msg_id.decode(),
                            "data": data,
                        }

        except Exception as e:
            yield {"data": json.dumps({"type": "error", "message": str(e)})}
            yield {"data": "[DONE]"}
        finally:
            await client.aclose()

    return EventSourceResponse(event_generator())
