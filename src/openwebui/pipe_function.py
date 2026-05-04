"""
BetterRAG Open WebUI Pipe Function.

Streams SSE responses from the BetterRAG API to Open WebUI, emitting
citation pills and file attachments via __event_emitter__.

Deploy: Open WebUI → Admin → Functions → + → paste this file.

NOTE: `async def pipe` must return a StreamingResponse, NOT be an async
generator (yield). The async-generator pattern causes the UI to hang in
"executing" state indefinitely (open-webui/open-webui#20196).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import httpx
from pydantic import BaseModel
from starlette.responses import StreamingResponse

_use_secrets = os.getenv("USE_MOUNTED_SECRETS", "auto").lower()
if _use_secrets == "false":
    SECRETS_DIR = None
elif _use_secrets == "true":
    SECRETS_DIR = Path("/mnt/secrets")
else:
    _candidate = Path("/mnt/secrets")
    SECRETS_DIR = _candidate if _candidate.is_dir() else None


def _get_secret(name: str, default: str = "") -> str:
    """Read a secret from /mnt/secrets/<name>, falling back to env var."""
    if SECRETS_DIR:
        secret_file = SECRETS_DIR / name
        if secret_f1ile.is_file():
            value = secret_file.read_text(encoding="utf-8").strip()
            if value:
                return value
    return os.getenv(name, default)


def _sse_token(content: str) -> bytes:
    """Encode a text token as an OpenAI-compatible SSE chunk."""
    chunk = {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
    return f"data: {json.dumps(chunk)}\n\n".encode()


class Pipe:
    class Valves(BaseModel):
        api_base_url: str = "http://localhost:8000"
        api_key: str = ""
        stream_timeout: int = 120
        show_citations: bool = True
        show_metadata: bool = False
        max_citations: int = 8
        use_direct_retrieval: bool = False  # True → /api/v1/chat/direct (query_rag), False → /api/v1/chat/stream (orchestrator, no Celery)

    def __init__(self):
        self.valves = self.Valves(
            api_base_url=_get_secret("BETTER_RAG_API_URL", "http://localhost:8000"),
            api_key=_get_secret("BETTER_RAG_API_KEY", ""),
        )
        self.type = "pipe"
        self.name = ""
        self.id = "better-rag"

    def pipes(self) -> list[dict]:
        return [{"id": "better-rag", "name": "BetterRAG"}]

    async def pipe(
        self,
        body: dict[str, Any],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> StreamingResponse:
        """
        Main entry point. Returns a StreamingResponse that streams tokens and
        emits citations as Open WebUI side-channel events.

        Event flow:
          status → [tokens...] → citations (via __event_emitter__) → files
        """
        messages = body.get("messages", [])
        user = __user__ or {}
        # Prefer Entra Object ID from OIDC (requires OAUTH_SUB_CLAIM=oid on OpenWebUI)
        user_id = (
            user.get("oauth", {}).get("microsoft", {}).get("sub")
            or user.get("id", "anonymous")
        )
        user_email = user.get("email", "")

        query = _last_user_message(messages)
        if not query:
            async def _empty() -> AsyncIterator[bytes]:
                yield _sse_token("No message provided.")
                yield b"data: [DONE]\n\n"
            return StreamingResponse(_empty(), media_type="text/event-stream")

        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Searching knowledge base…", "done": False},
            })

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-User-Id": user_id,
            "X-User-Email": user_email,
        }
        if self.valves.api_key:
            headers["Authorization"] = f"Bearer {self.valves.api_key}"

        payload = {
            "messages": messages,
            "user_id": user_id,
            "stream": True,
        }

        endpoint = (
            "/api/v1/chat/direct"
            if self.valves.use_direct_retrieval
            else "/api/v1/chat/stream"
        )

        return StreamingResponse(
            self._stream(payload, headers, endpoint, __event_emitter__),
            media_type="text/event-stream",
        )

    async def _stream(
        self,
        payload: dict,
        headers: dict,
        endpoint: str,
        __event_emitter__: Optional[Callable],
    ) -> AsyncIterator[bytes]:
        """
        Async generator that consumes BetterRAG SSE events and yields
        OpenAI-format SSE bytes for token content. Non-token events
        (status, citation, metadata, file) are forwarded via __event_emitter__.
        """
        documents: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=self.valves.stream_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.valves.api_base_url}{endpoint}",
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue

                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break

                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        etype = event.get("type", "token")

                        if etype == "token":
                            yield _sse_token(event.get("content", ""))

                        elif etype == "status":
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "status",
                                    "data": {
                                        "description": event.get("message", ""),
                                        "done": event.get("done", False),
                                    },
                                })

                        elif etype == "citation" and self.valves.show_citations:
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "citation",
                                    "data": {
                                        "document": [event.get("content", "")],
                                        "metadata": [{
                                            "source": event.get("sharepoint_url", ""),
                                            "title": event.get("title", ""),
                                            "section": event.get("section", ""),
                                            "department": event.get("department", ""),
                                            "file_type": event.get("file_type", ""),
                                            "pages": event.get("pages", []),
                                        }],
                                        "source": {
                                            "name": event.get("title", ""),
                                            "url":  event.get("sharepoint_url", ""),
                                        },
                                    },
                                })

                        elif etype == "file":
                            documents.append(event)

                        elif etype == "metadata":
                            if self.valves.show_metadata and __event_emitter__:
                                dept = event.get("department", "")
                                strategy = event.get("retrieval_strategy", "")
                                model = event.get("model", "")
                                await __event_emitter__({
                                    "type": "status",
                                    "data": {
                                        "description": (
                                            f"Routed to **{dept}** agent · "
                                            f"`{strategy}` retrieval · `{model}`"
                                        ),
                                        "done": True,
                                    },
                                })

                        elif etype == "error":
                            msg = event.get("message", "Unknown error")
                            if __event_emitter__:
                                await __event_emitter__({
                                    "type": "status",
                                    "data": {"description": f"⚠️ {msg}", "done": True},
                                })
                            yield _sse_token(f"\n\n> ⚠️ **Error:** {msg}")
                            yield b"data: [DONE]\n\n"
                            return

        except httpx.TimeoutException:
            yield _sse_token(
                "\n\n> ⚠️ **Request timed out.** The query may be too complex — try a more specific question."
            )
            yield b"data: [DONE]\n\n"
            return
        except httpx.HTTPStatusError as exc:
            yield _sse_token(f"\n\n> ⚠️ **API error {exc.response.status_code}.** Please try again.")
            yield b"data: [DONE]\n\n"
            return
        except Exception as exc:
            yield _sse_token(f"\n\n> ⚠️ **Unexpected error:** {exc}")
            yield b"data: [DONE]\n\n"
            return

        # Emit generated documents as status links
        for doc in documents:
            fname = doc.get("filename", "file")
            url = doc.get("download_url", "")
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {
                        "description": f"📎 Generated: [{fname}]({self.valves.api_base_url}{url})",
                        "done": True,
                    },
                })
            yield _sse_token(
                f"\n\n📎 **Generated:** "
                f"[{doc.get('filename', 'Download')}]"
                f"({self.valves.api_base_url}{doc.get('download_url', '')})"
            )

        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Done", "done": True},
            })

        yield b"data: [DONE]\n\n"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Multi-modal content block — extract text parts
                return " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
    return ""
