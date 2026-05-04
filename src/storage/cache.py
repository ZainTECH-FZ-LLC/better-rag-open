"""Redis caching layer for HyDE embeddings, RBAC lookups, and query dedup."""

from __future__ import annotations

import hashlib
import json

import redis.asyncio as aioredis
import structlog

from config.settings import get_settings

logger = structlog.get_logger()

_cache: aioredis.Redis | None = None


async def get_cache() -> aioredis.Redis:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = aioredis.from_url(settings.redis_cache_url)
    return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.aclose()
        _cache = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── HyDE Embedding Cache ──

async def cache_hyde_embedding(query: str, embedding: list[float]) -> None:
    """Cache a HyDE-generated embedding (1 hour TTL)."""
    settings = get_settings()
    cache = await get_cache()
    key = f"cache:hyde:{_hash(query)}"
    await cache.setex(key, settings.CACHE_HYDE_TTL, json.dumps(embedding))


async def get_cached_hyde_embedding(query: str) -> list[float] | None:
    cache = await get_cache()
    key = f"cache:hyde:{_hash(query)}"
    result = await cache.get(key)
    return json.loads(result) if result else None


# ── User RBAC Cache ──

async def cache_user_rbac(user_id: str, document_ids: list[str]) -> None:
    """Cache user's accessible document IDs (5 min TTL)."""
    settings = get_settings()
    cache = await get_cache()
    key = f"cache:rbac:{user_id}"
    await cache.setex(key, settings.CACHE_RBAC_TTL, json.dumps(document_ids))


async def get_cached_user_rbac(user_id: str) -> set[str] | None:
    cache = await get_cache()
    key = f"cache:rbac:{user_id}"
    result = await cache.get(key)
    return set(json.loads(result)) if result else None


async def invalidate_rbac_cache_for_document(document_id: str) -> None:
    """Invalidate all RBAC caches (brute force — acceptable at 5 min TTL)."""
    cache = await get_cache()
    cursor = b"0"
    while True:
        cursor, keys = await cache.scan(cursor, match="cache:rbac:*", count=100)
        if keys:
            await cache.delete(*keys)
        if cursor == b"0":
            break


# ── Query Analysis Cache ──

async def cache_query_analysis(query: str, department: str | None, data: dict) -> None:
    """Cache LLM query analysis result (2-min TTL)."""
    settings = get_settings()
    cache = await get_cache()
    key = f"cache:qanalysis:{_hash(query + ':' + (department or ''))}"
    await cache.setex(key, settings.CACHE_QUERY_TTL, json.dumps(data))


async def get_cached_query_analysis(query: str, department: str | None) -> dict | None:
    cache = await get_cache()
    key = f"cache:qanalysis:{_hash(query + ':' + (department or ''))}"
    result = await cache.get(key)
    return json.loads(result) if result else None


# ── Query Dedup Cache ──

async def cache_query_result(query_hash: str, result: dict) -> None:
    """Deduplicate identical queries within a 2-min window."""
    settings = get_settings()
    cache = await get_cache()
    key = f"cache:query:{query_hash}"
    await cache.setex(key, settings.CACHE_QUERY_TTL, json.dumps(result))


async def get_cached_query_result(query_hash: str) -> dict | None:
    cache = await get_cache()
    key = f"cache:query:{query_hash}"
    result = await cache.get(key)
    return json.loads(result) if result else None
