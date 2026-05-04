"""
Simple LLM Open WebUI Pipe Function.

Forwards messages directly to the BetterRAG backend's plain LLM endpoint
(/api/v1/chat/llm) with no RAG, no system prompt, and no citations.

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
        if secret_file.is_file():
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

    def __init__(self):
        self.valves = self.Valves(
            api_base_url=_get_secret("BETTER_RAG_API_URL", "http://localhost:8000"),
            api_key=_get_secret("BETTER_RAG_API_KEY", ""),
        )
        self.type = "pipe"
        self.name = ""
        self.id = "simple-llm"

    def pipes(self) -> list[dict]:
        return [{"id": "simple-llm", "name": "Simple LLM"}]

    async def pipe(
        self,
        body: dict[str, Any],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> StreamingResponse:
        """
        Main entry point. Forwards the full messages array to /api/v1/chat/llm
        and streams tokens back to Open WebUI.
        """
        messages = body.get("messages", [])

        if not messages:
            async def _empty() -> AsyncIterator[bytes]:
                yield _sse_token("No message provided.")
                yield b"data: [DONE]\n\n"
            return StreamingResponse(_empty(), media_type="text/event-stream")

        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.valves.api_key:
            headers["Authorization"] = f"Bearer {self.valves.api_key}"

        payload = {"messages": messages, "stream": True}

        return StreamingResponse(
            self._stream(payload, headers, __event_emitter__),
            media_type="text/event-stream",
        )

    async def _stream(
        self,
        payload: dict,
        headers: dict,
        __event_emitter__: Optional[Callable],
    ) -> AsyncIterator[bytes]:
        """
        Async generator that consumes SSE token events from /api/v1/chat/llm
        and yields OpenAI-format SSE bytes.
        """
        try:
            async with httpx.AsyncClient(timeout=self.valves.stream_timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.valves.api_base_url}/api/v1/chat/llm",
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
                "\n\n> ⚠️ **Request timed out.** Please try again."
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

        yield b"data: [DONE]\n\n"
