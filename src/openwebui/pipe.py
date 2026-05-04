"""
Open WebUI Pipe Function for BetterRAG.

This module implements the OpenWebUI Pipe Function interface that bridges
Open WebUI to the BetterRAG backend. It streams SSE events from the RAG
pipeline back to the frontend.

Deploy by pasting this into Open WebUI Admin → Functions → Add Function,
or mount it as a Pipe Function via the Open WebUI Pipelines extension.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import requests

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


class Pipe:
    """Open WebUI Pipe Function for BetterRAG enterprise RAG agent."""

    class Valves:
        """User-configurable settings in Open WebUI admin panel."""

        def __init__(self):
            self.BETTER_RAG_API_URL = _get_secret(
                "BETTER_RAG_API_URL", "http://api:8000"
            )
            self.BETTER_RAG_API_KEY = _get_secret("BETTER_RAG_API_KEY", "")
            self.REQUEST_TIMEOUT = int(_get_secret("BETTER_RAG_TIMEOUT", "120"))

    def __init__(self):
        self.valves = self.Valves()
        self.name = "BetterRAG Agent"
        self.id = "better-rag-agent"

    def pipe(
        self,
        body: dict[str, Any],
    ) -> Generator[str, None, None] | str:
        """
        Main entry point called by Open WebUI for each user message.

        Streams SSE events from BetterRAG API and translates them to
        Open WebUI's expected format:
        - Markdown text (streamed token by token)
        - Citations as markdown links
        - File download links
        """
        messages = body.get("messages", [])
        user = body.get("user", {})
        # Prefer Entra Object ID from OIDC (requires OAUTH_SUB_CLAIM=oid on OpenWebUI)
        user_id = (
            user.get("oauth", {}).get("microsoft", {}).get("sub")
            or user.get("id", "anonymous")
        )
        user_email = user.get("email", "")

        headers = {
            "Content-Type": "application/json",
            "X-User-Id": user_id,
            "X-User-Email": user_email,
        }
        if self.valves.BETTER_RAG_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.BETTER_RAG_API_KEY}"

        api_url = f"{self.valves.BETTER_RAG_API_URL}/api/v1/chat/stream"

        try:
            response = requests.post(
                api_url,
                headers=headers,
                json={"messages": messages},
                stream=True,
                timeout=self.valves.REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            citations_emitted = False

            for line in response.iter_lines():
                if not line:
                    continue

                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue

                data_str = decoded[6:]  # Remove "data: " prefix

                if data_str == "[DONE]":
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type == "token":
                    # Stream tokens directly
                    yield event.get("content", "")

                elif event_type == "citation":
                    # Emit citation as markdown link
                    if not citations_emitted:
                        yield "\n\n---\n**Sources:**\n"
                        citations_emitted = True

                    title = event.get("title", "Source")
                    url = event.get("sharepoint_url", "")
                    dept = event.get("department", "")
                    section = event.get("section", "")

                    parts = [f"[{title}]({url})"]
                    if dept:
                        parts.append(f"({dept})")
                    if section:
                        parts.append(f"— {section}")

                    yield f"- {' '.join(parts)}\n"

                elif event_type == "file":
                    # Emit file download link
                    filename = event.get("filename", "file")
                    download_url = event.get("download_url", "")
                    mime_type = event.get("mime_type", "")

                    yield (
                        f"\n\n📎 **Generated File:** "
                        f"[{filename}]({download_url})"
                    )

                elif event_type == "status":
                    # Status updates — skip unless it's an error
                    pass

                elif event_type == "error":
                    yield f"\n\n**Error:** {event.get('message', 'Unknown error')}"

        except requests.exceptions.Timeout:
            yield "\n\n**Error:** Request timed out. Please try again."
        except requests.exceptions.ConnectionError:
            yield (
                "\n\n**Error:** Cannot connect to BetterRAG API. "
                "Please check that the service is running."
            )
        except Exception as e:
            yield f"\n\n**Error:** {str(e)}"
