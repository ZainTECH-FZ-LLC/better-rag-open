"""Customer Care API routes — SSE query endpoint for internal support agents."""

from __future__ import annotations

import json
from typing import Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/cc", tags=["customer-care"])

_VALID_CHANNELS = {"chat", "email", "phone"}


class CCQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="The customer care agent's question")
    channel: Literal["chat", "email", "phone"] = Field(
        default="chat",
        description="Communication channel — shapes the tone of the generated script",
    )


@router.post("/query")
async def cc_query(request: Request, body: CCQueryRequest):
    """
    Customer Care query endpoint — SSE stream.

    Runs the full CC retrieval pipeline (HyDE/cosine/MMR + reranking + graph expansion)
    against the Customer Care knowledge base, then generates an adaptive structured
    response. Only relevant sections are included in the response.

    SSE event types:
      {"type": "status",        "message": "...", "step": "..."}
      {"type": "token",         "content": "..."}           — always present
      {"type": "cc_policy_link","title": "...", "url": "..."}   — if relevant
      {"type": "cc_script",     "channel": "...", "content": "..."}  — if relevant
      {"type": "cc_upsell",     "product": "...", "pitch": "..."}    — if relevant
      {"type": "error",         "message": "..."}
      data: [DONE]

    Requires X-User-Id header for RBAC-filtered retrieval.
    Different customer care teams will see only the CC documents they have access to.
    """
    user_id = request.headers.get("X-User-Id", "anonymous")

    async def event_generator():
        try:
            from src.customer_care.agent import CustomerCareAgent

            agent = CustomerCareAgent()
            async for event in agent.stream_answer(
                question=body.question,
                user_id=user_id,
                channel=body.channel,
            ):
                yield f"data: {json.dumps(event)}\n\n"

        except Exception as exc:
            logger.error(
                "cc_query.error",
                error=str(exc),
                user_id=user_id,
                question=body.question[:100],
            )
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    logger.info(
        "cc_query.received",
        user_id=user_id,
        channel=body.channel,
        question_length=len(body.question),
    )

    return StreamingResponse(event_generator(), media_type="text/event-stream")
