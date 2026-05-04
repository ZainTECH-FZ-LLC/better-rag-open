"""Quick connectivity test for all configured services."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings

settings = get_settings()


def section(title: str):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


def ok(msg: str):
    print(f"  [OK]  {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")


# ── 1. PostgreSQL ─────────────────────────────────────
async def test_postgres():
    section("PostgreSQL + pgvector")
    try:
        import asyncpg
        conn = await asyncpg.connect(
            host=settings.PGVECTOR_HOST,
            port=settings.PGVECTOR_PORT,
            database=settings.PGVECTOR_DATABASE,
            user=settings.PGVECTOR_USER,
            password=settings.PGVECTOR_PASSWORD,
            ssl="require",
        )
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        ok(f"Connected — {version[:50]}")

        conn = await asyncpg.connect(
            host=settings.PGVECTOR_HOST,
            port=settings.PGVECTOR_PORT,
            database=settings.PGVECTOR_DATABASE,
            user=settings.PGVECTOR_USER,
            password=settings.PGVECTOR_PASSWORD,
            ssl="require",
        )
        ext = await conn.fetchval(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        )
        await conn.close()
        if ext:
            ok("pgvector extension installed")
        else:
            fail("pgvector extension NOT installed — run: CREATE EXTENSION vector;")
    except Exception as e:
        fail(str(e))


# ── 2. Azure OpenAI (LLM) ────────────────────────────
async def test_llm():
    section("Azure OpenAI — LLM")
    print(f"  endpoint   : {settings.AZURE_OPENAI_ENDPOINT}")
    print(f"  api_version: {settings.AZURE_OPENAI_API_VERSION}")
    print(f"  model      : {settings.LLM_CHEAP_MODEL}")
    print(f"  key (last8): ...{settings.AZURE_OPENAI_API_KEY[-8:]}")
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": "Reply with just: OK"}],
            max_completion_tokens=5,
        )
        reply = response.choices[0].message.content.strip()[:80]
        ok(f"Model '{settings.LLM_CHEAP_MODEL}' responded: {reply}")
    except Exception as e:
        fail(str(e))


# ── 3. Azure OpenAI (Embedding) ──────────────────────
async def test_embedding():
    section("Azure OpenAI — Embedding")
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.EMBEDDING_AZURE_ENDPOINT,
            api_key=settings.EMBEDDING_AZURE_API_KEY,
            api_version=settings.EMBEDDING_AZURE_API_VERSION,
        )
        response = client.embeddings.create(
            model=settings.EMBEDDING_AZURE_DEPLOYMENT,
            input="test",
            dimensions=settings.EMBEDDING_DIMENSIONS,
        )
        dims = len(response.data[0].embedding)
        ok(f"Deployment '{settings.EMBEDDING_AZURE_DEPLOYMENT}' returned {dims}-dim vector")
    except Exception as e:
        fail(str(e))


# ── 4. Azure Document Intelligence ───────────────────
async def test_doc_intelligence():
    section("Azure Document Intelligence")
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
        DocumentIntelligenceClient(
            endpoint=settings.OCR_AZURE_ENDPOINT,
            credential=AzureKeyCredential(settings.OCR_AZURE_KEY),
        )
        ok(f"Client created for {settings.OCR_AZURE_ENDPOINT}")
    except Exception as e:
        fail(str(e))


# ── 5. Redis ─────────────────────────────────────────
async def test_redis():
    section("Redis")
    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            ssl=settings.REDIS_SSL,
            socket_connect_timeout=5,
        )
        pong = await r.ping()
        await r.aclose()
        ok(f"Ping: {pong}")
    except Exception as e:
        fail(str(e))


# ── 6. Neo4j ─────────────────────────────────────────
async def test_neo4j():
    section("Neo4j")
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            connection_timeout=5,
        )
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS n")
            record = await result.single()
            ok(f"Query returned: {record['n']}")
        await driver.close()
    except Exception as e:
        fail(str(e))


async def main():
    print("\nbetter-rag connectivity tests")
    await test_postgres()
    await test_llm()
    await test_embedding()
    await test_doc_intelligence()
    await test_redis()
    await test_neo4j()
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
