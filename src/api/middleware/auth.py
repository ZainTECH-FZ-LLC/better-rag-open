"""API key + user header validation middleware."""

from __future__ import annotations

import json

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import get_settings

logger = structlog.get_logger()

# Paths that bypass API key validation
_PUBLIC_PATHS = frozenset({
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
})

_PREFIX_BYPASS = (
    "/webhooks/",          # webhook clientState validation handles auth
    "/api/v1/files/",      # static file serving, optionally gated elsewhere
)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Validate API key (Bearer token) and extract user context.

    Headers read:
    - Authorization: Bearer <BETTER_RAG_API_KEY>
    - X-User-Id: Entra Object ID forwarded from Open WebUI
    - X-User-Email: UPN / email forwarded from Open WebUI

    If BETTER_RAG_API_KEY is empty (dev mode), auth is skipped.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path

        # Public paths — no auth required
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Prefix-based bypass
        if any(path.startswith(p) for p in _PREFIX_BYPASS):
            return await call_next(request)

        # Validate Bearer token when configured
        if settings.BETTER_RAG_API_KEY:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return _unauthorized("Missing Authorization header")

            token = auth_header.removeprefix("Bearer ").strip()
            if token != settings.BETTER_RAG_API_KEY:
                logger.warning(
                    "auth.invalid_token",
                    path=path,
                    user_id=request.headers.get("X-User-Id", ""),
                )
                return _unauthorized("Invalid API key")

        # Extract and validate user context (best-effort — not enforced if absent)
        user_id = request.headers.get("X-User-Id", "").strip()
        if not user_id:
            user_id = "anonymous"

        # Stash on request state for downstream handlers
        request.state.user_id = user_id
        request.state.user_email = request.headers.get("X-User-Email", "").strip()

        return await call_next(request)


def _unauthorized(detail: str) -> Response:
    return Response(
        content=json.dumps({"error": "Unauthorized", "detail": detail}),
        status_code=401,
        media_type="application/json",
    )


def get_user_id(request: Request) -> str:
    """Dependency helper — returns the authenticated user ID from request state."""
    return getattr(request.state, "user_id", "anonymous")


def get_user_email(request: Request) -> str:
    """Dependency helper — returns the user email from request state."""
    return getattr(request.state, "user_email", "")
