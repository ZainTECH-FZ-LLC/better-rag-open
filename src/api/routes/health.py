"""Health check router — liveness, readiness, and deep dependency checks."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from config.settings import get_settings

logger = structlog.get_logger()
router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
async def liveness() -> dict:
    """
    Liveness probe — returns 200 if the process is alive.
    Used by Kubernetes to decide whether to restart the pod.
    """
    return {"status": "ok", "app": settings.APP_NAME}


@router.get("/health/ready")
async def readiness() -> Response:
    """
    Readiness probe — checks that dependencies are reachable.
    Used by Kubernetes to decide whether to send traffic to this pod.
    Returns 200 when ready, 503 when degraded.
    """
    checks = await _run_checks()
    all_ok = all(c["ok"] for c in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        },
        status_code=status_code,
    )


@router.get("/health/live")
async def deep_health() -> Response:
    """
    Deep health check with timing — for monitoring dashboards.
    Always returns 200 (never crashes the pod), but surfaces degradation.
    """
    start = time.monotonic()
    checks = await _run_checks()
    duration_ms = int((time.monotonic() - start) * 1000)
    all_ok = all(c["ok"] for c in checks.values())

    return JSONResponse(
        content={
            "status": "healthy" if all_ok else "degraded",
            "duration_ms": duration_ms,
            "checks": checks,
        },
        status_code=200,
    )


# ── Dependency checks ─────────────────────────────────────────────────────────

async def _run_checks() -> dict[str, dict[str, Any]]:
    """Run all dependency checks concurrently."""
    pg_check, redis_check, neo4j_check = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        _check_neo4j(),
        return_exceptions=True,
    )
    return {
        "postgres": pg_check if isinstance(pg_check, dict) else {"ok": False, "error": str(pg_check)},
        "redis":    redis_check if isinstance(redis_check, dict) else {"ok": False, "error": str(redis_check)},
        "neo4j":    neo4j_check if isinstance(neo4j_check, dict) else {"ok": False, "error": str(neo4j_check)},
    }


async def _check_postgres() -> dict[str, Any]:
    try:
        from src.storage.db import get_db_session
        t0 = time.monotonic()
        async with get_db_session() as db:
            await db.execute("SELECT 1")
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        logger.warning("health.postgres_failed", error=str(exc))
        return {"ok": False, "error": str(exc)}


async def _check_redis() -> dict[str, Any]:
    try:
        import redis.asyncio as aioredis
        t0 = time.monotonic()
        client = aioredis.from_url(settings.redis_url(db=0))
        await client.ping()
        await client.aclose()
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        logger.warning("health.redis_failed", error=str(exc))
        return {"ok": False, "error": str(exc)}


async def _check_neo4j() -> dict[str, Any]:
    try:
        from neo4j import AsyncGraphDatabase
        t0 = time.monotonic()
        driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        await driver.verify_connectivity()
        await driver.close()
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        logger.warning("health.neo4j_failed", error=str(exc))
        return {"ok": False, "error": str(exc)}
