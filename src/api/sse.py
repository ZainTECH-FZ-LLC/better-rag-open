"""SSE event formatting utilities for the BetterRAG streaming API."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


# ── Event type constants ──────────────────────────────────────────────────────

class EventType:
    TOKEN = "token"
    CITATION = "citation"
    FILE = "file"
    STATUS = "status"
    ERROR = "error"
    DONE = "done"
    METADATA = "metadata"


# ── Typed event builders ──────────────────────────────────────────────────────

def token_event(content: str) -> str:
    """Stream a single text token."""
    return _encode({"type": EventType.TOKEN, "content": content})


def citation_event(
    title: str,
    sharepoint_url: str,
    content: str,
    section: str | None = None,
    department: str | None = None,
    score: float = 0.0,
    document_id: str = "",
) -> str:
    """Emit a single source citation."""
    return _encode({
        "type": EventType.CITATION,
        "title": title,
        "sharepoint_url": sharepoint_url,
        "content": content[:400],  # preview snippet
        "section": section or "",
        "department": department or "",
        "score": round(score, 3),
        "document_id": document_id,
    })


def file_event(
    filename: str,
    download_url: str,
    mime_type: str,
    file_type: str,
) -> str:
    """Emit a generated document download link."""
    return _encode({
        "type": EventType.FILE,
        "filename": filename,
        "download_url": download_url,
        "mime_type": mime_type,
        "file_type": file_type,
    })


def status_event(message: str, step: str = "", done: bool = False) -> str:
    """Emit a pipeline status update (shown as a spinner in the UI)."""
    return _encode({
        "type": EventType.STATUS,
        "message": message,
        "step": step,
        "done": done,
    })


def error_event(message: str, code: str = "INTERNAL_ERROR") -> str:
    """Emit an error event that stops the stream."""
    return _encode({
        "type": EventType.ERROR,
        "message": message,
        "code": code,
    })


def metadata_event(
    query_type: str,
    department: str,
    retrieval_strategy: str,
    model: str,
    duration_ms: int = 0,
) -> str:
    """Emit pipeline metadata (routing decisions, model used, timing)."""
    return _encode({
        "type": EventType.METADATA,
        "query_type": query_type,
        "department": department,
        "retrieval_strategy": retrieval_strategy,
        "model": model,
        "duration_ms": duration_ms,
    })


def done_event() -> str:
    """Terminal event — signals the stream is complete."""
    return "data: [DONE]\n\n"


# ── Batch formatters ──────────────────────────────────────────────────────────

def citations_from_results(results: list[Any]) -> list[str]:
    """
    Convert a list of ChunkResult / retrieval result objects to citation events.

    Accepts objects with attributes or dicts.
    """
    events = []
    seen_docs: set[str] = set()

    for r in results:
        if isinstance(r, dict):
            doc_id = r.get("document_id", r.get("title", ""))
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            events.append(citation_event(
                title=r.get("document_title", r.get("title", "Unknown")),
                sharepoint_url=r.get("sharepoint_url", ""),
                content=r.get("content", ""),
                section=r.get("section_heading", r.get("section")),
                department=r.get("department"),
                score=r.get("score", 0.0),
                document_id=doc_id,
            ))
        else:
            doc_id = getattr(r, "document_id", "")
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            events.append(citation_event(
                title=getattr(r, "document_title", "Unknown"),
                sharepoint_url=getattr(r, "sharepoint_url", ""),
                content=getattr(r, "content", ""),
                section=getattr(r, "section_heading", None),
                department=getattr(r, "department", None),
                score=getattr(r, "score", 0.0),
                document_id=doc_id,
            ))

    return events


def files_from_documents(documents: list[dict]) -> list[str]:
    """Convert a list of GeneratedDocument dicts to file events."""
    return [
        file_event(
            filename=d.get("filename", ""),
            download_url=d.get("download_url", ""),
            mime_type=d.get("mime_type", "application/octet-stream"),
            file_type=d.get("file_type", ""),
        )
        for d in documents
        if d.get("filename")
    ]


# ── Internal ──────────────────────────────────────────────────────────────────

def _encode(payload: dict) -> str:
    """Encode a payload dict as an SSE data line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── OpenAI-compat adapter ─────────────────────────────────────────────────────

def to_openai_chunk(content: str, model: str = "better-rag", finish: bool = False) -> str:
    """
    Wrap a content token as an OpenAI-compatible chat.completion.chunk SSE event.

    Required for Open WebUI's OpenAI-compat mode.
    """
    delta: dict = {"content": content} if not finish else {}
    chunk = {
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": "stop" if finish else None,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"
