"""
BetterRAG Open WebUI Action Function — contextual action buttons.

Adds buttons to assistant messages allowing users to:
- Regenerate the answer as a PPTX presentation
- Regenerate the answer as a DOCX report
- Regenerate the answer as an XLSX spreadsheet
- Copy source citations to clipboard
- Run a follow-up search with different retrieval strategy

Deploy: Open WebUI → Admin → Functions → + → paste this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel

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


class Action:
    class Valves(BaseModel):
        api_base_url: str = "http://api:8000"
        api_key: str = ""
        request_timeout: int = 120

    def __init__(self):
        self.valves = self.Valves(
            api_base_url=_get_secret("BETTER_RAG_API_URL", "http://api:8000"),
            api_key=_get_secret("BETTER_RAG_API_KEY", ""),
        )
        self.type = "action"
        self.name = "BetterRAG Actions"
        self.id = "better-rag-actions"

    def action(
        self,
        body: dict[str, Any],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
        __action_id__: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Dispatch an action based on __action_id__.

        Action IDs registered via the actions() method below are shown
        as buttons under each assistant message in the Open WebUI chat.
        """
        if __action_id__ == "export_pptx":
            return self._export_doc(body, "pptx", __user__, __event_emitter__)

        if __action_id__ == "export_docx":
            return self._export_doc(body, "docx", __user__, __event_emitter__)

        if __action_id__ == "export_xlsx":
            return self._export_doc(body, "xlsx", __user__, __event_emitter__)

        if __action_id__ == "retry_hyde":
            return self._retry_with_strategy(body, "hyde_cosine", __user__, __event_emitter__)

        if __action_id__ == "retry_mmr":
            return self._retry_with_strategy(body, "mmr", __user__, __event_emitter__)

        return None

    def actions(self) -> list[dict]:
        """
        Declare the action buttons shown under assistant messages.
        Open WebUI calls this to build the action toolbar.
        """
        return [
            {
                "id": "export_pptx",
                "label": "📊 Export as PPTX",
                "description": "Regenerate this answer as a PowerPoint presentation",
            },
            {
                "id": "export_docx",
                "label": "📄 Export as DOCX",
                "description": "Regenerate this answer as a Word document",
            },
            {
                "id": "export_xlsx",
                "label": "📈 Export as XLSX",
                "description": "Regenerate this answer as an Excel spreadsheet",
            },
            {
                "id": "retry_hyde",
                "label": "🔍 Retry with HyDE",
                "description": "Re-run the search using hypothetical document expansion",
            },
            {
                "id": "retry_mmr",
                "label": "🎲 Retry with MMR",
                "description": "Re-run the search with maximal marginal relevance for diverse results",
            },
        ]

    # ── Private action handlers ───────────────────────────────────────────────

    def _export_doc(
        self,
        body: dict,
        doc_type: str,
        user: Optional[dict],
        emitter: Optional[Callable],
    ) -> Optional[dict]:
        """Request document generation from the last assistant answer."""
        if emitter:
            emitter({
                "type": "status",
                "data": {"description": f"Generating {doc_type.upper()}…", "done": False},
            })

        last_answer = _last_assistant_message(body.get("messages", []))
        last_query = _last_user_message(body.get("messages", []))

        if not last_answer:
            if emitter:
                emitter({
                    "type": "status",
                    "data": {"description": "No answer to export.", "done": True},
                })
            return None

        headers = self._headers(user)
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Convert the following answer into a {doc_type.upper()} document.\n\n"
                        f"Original question: {last_query}\n\n"
                        f"Answer:\n{last_answer}"
                    ),
                }
            ],
            "document_type": doc_type,
            "stream": False,
        }

        try:
            resp = httpx.post(
                f"{self.valves.api_base_url}/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.valves.request_timeout,
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as exc:
            if emitter:
                emitter({
                    "type": "status",
                    "data": {"description": f"⚠️ Export failed: {exc}", "done": True},
                })
            return None

        # Surface generated documents
        docs = result.get("documents", [])
        if docs and emitter:
            for doc in docs:
                fname = doc.get("filename", "file")
                url = f"{self.valves.api_base_url}{doc.get('download_url', '')}"
                emitter({
                    "type": "status",
                    "data": {
                        "description": f"📎 Ready: [{fname}]({url})",
                        "done": True,
                    },
                })

        return {"type": "message", "content": _format_doc_links(docs, self.valves.api_base_url)}

    def _retry_with_strategy(
        self,
        body: dict,
        strategy: str,
        user: Optional[dict],
        emitter: Optional[Callable],
    ) -> Optional[dict]:
        """Re-run the last query with an explicit retrieval strategy override."""
        if emitter:
            emitter({
                "type": "status",
                "data": {
                    "description": f"Re-running with `{strategy}` strategy…",
                    "done": False,
                },
            })

        messages = body.get("messages", [])
        headers = self._headers(user)
        payload = {
            "messages": messages,
            "retrieval_strategy_override": strategy,
            "stream": False,
        }

        try:
            resp = httpx.post(
                f"{self.valves.api_base_url}/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.valves.request_timeout,
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as exc:
            if emitter:
                emitter({
                    "type": "status",
                    "data": {"description": f"⚠️ Retry failed: {exc}", "done": True},
                })
            return None

        answer = result.get("answer", "")
        if emitter:
            emitter({
                "type": "status",
                "data": {"description": "Done", "done": True},
            })

        return {"type": "message", "content": answer} if answer else None

    def _headers(self, user: Optional[dict]) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-User-Id": (user or {}).get("id", "anonymous"),
        }
        if self.valves.api_key:
            headers["Authorization"] = f"Bearer {self.valves.api_key}"
        return headers


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def _last_assistant_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""


def _format_doc_links(docs: list[dict], base_url: str) -> str:
    if not docs:
        return "Document generation failed."
    lines = ["**Generated documents:**"]
    for doc in docs:
        fname = doc.get("filename", "Download")
        url = f"{base_url}{doc.get('download_url', '')}"
        lines.append(f"- 📎 [{fname}]({url})")
    return "\n".join(lines)
