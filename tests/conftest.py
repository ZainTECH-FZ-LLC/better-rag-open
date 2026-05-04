"""Pytest fixtures for the better-rag test suite."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import Settings
from src.models.document import Document, DocumentChunk, DocumentUserAccess
from src.models.enums import ChunkType, ProcessingStatus
from src.storage.db import Base


# ── Settings Fixture ──

@pytest.fixture
def test_settings() -> Settings:
    """Return test settings with safe defaults."""
    return Settings(
        DEBUG=True,
        LOG_LEVEL="DEBUG",
        PGVECTOR_HOST="localhost",
        PGVECTOR_PORT=5432,
        PGVECTOR_DATABASE="betterrag_test",
        PGVECTOR_USER="betterrag",
        PGVECTOR_PASSWORD="testpass",
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="testpass",
        REDIS_HOST="localhost",
        REDIS_PORT=6379,
        GRAPH_TENANT_ID="test-tenant",
        GRAPH_CLIENT_ID="test-client",
        GRAPH_CLIENT_SECRET="test-secret",
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        AZURE_OPENAI_API_KEY="test-key",
        EMBEDDING_AZURE_ENDPOINT="https://test.openai.azure.com/",
        EMBEDDING_AZURE_API_KEY="test-key",
        BLOB_ACCOUNT_URL="https://test.blob.core.windows.net",
        OCR_AZURE_ENDPOINT="https://test.cognitiveservices.azure.com/",
        OCR_AZURE_KEY="test-key",
    )


# ── Sample Data Factories ──

@pytest.fixture
def sample_document() -> Document:
    """Create a sample Document model instance."""
    return Document(
        id=uuid.uuid4(),
        drive_id="drive-001",
        drive_item_id="item-001",
        site_id="site-001",
        name="Q4 Financial Report.pdf",
        file_type="pdf",
        size_bytes=1024000,
        mime_type="application/pdf",
        sharepoint_url="https://contoso.sharepoint.com/sites/finance/Q4Report.pdf",
        parent_path="/sites/finance/Shared Documents/Reports",
        ctag="c:{version1}",
        etag="e:{version1}",
        status=ProcessingStatus.COMPLETED,
        summary="Q4 financial report covering revenue, expenses, and profit margins.",
        department="finance",
        content_type_tag="report",
        topics={"topics": ["revenue", "expenses", "profit", "Q4"]},
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_chunks(sample_document: Document) -> list[dict]:
    """Sample chunk dicts for testing."""
    return [
        {
            "content": "Revenue for Q4 2025 reached $45.2M, a 12% increase YoY.",
            "content_with_context": (
                "Document Summary: Q4 financial report.\n\n"
                "Revenue for Q4 2025 reached $45.2M, a 12% increase YoY."
            ),
            "chunk_type": ChunkType.TEXT,
            "sequence_number": 0,
            "page_numbers": [1],
            "section_heading": "Revenue Overview",
            "token_count": 45,
        },
        {
            "content": "Operating expenses increased to $28.1M due to new hires.",
            "content_with_context": (
                "Document Summary: Q4 financial report.\n\n"
                "Operating expenses increased to $28.1M due to new hires."
            ),
            "chunk_type": ChunkType.TEXT,
            "sequence_number": 1,
            "page_numbers": [2],
            "section_heading": "Expenses",
            "token_count": 38,
        },
        {
            "content": "| Department | Budget | Actual |\n| Sales | $10M | $9.5M |\n| Marketing | $5M | $4.8M |",
            "content_with_context": (
                "Document Summary: Q4 financial report.\n\n"
                "| Department | Budget | Actual |\n| Sales | $10M | $9.5M |"
            ),
            "chunk_type": ChunkType.TABLE,
            "sequence_number": 2,
            "page_numbers": [3],
            "section_heading": "Budget vs Actual",
            "token_count": 52,
        },
    ]


@pytest.fixture
def sample_delta_items() -> list[dict]:
    """Sample Microsoft Graph delta API response items."""
    return [
        {
            "id": "item-new-001",
            "name": "New Policy.docx",
            "file": {
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            },
            "webUrl": "https://contoso.sharepoint.com/sites/hr/NewPolicy.docx",
            "parentReference": {
                "siteId": "site-001",
                "path": "/sites/hr/Shared Documents/Policies",
            },
            "cTag": "c:{new-version}",
            "eTag": "e:{new-version}",
            "size": 52000,
            "lastModifiedDateTime": "2026-02-22T10:00:00Z",
            "createdBy": {"user": {"email": "admin@contoso.com"}},
            "lastModifiedBy": {"user": {"email": "admin@contoso.com"}},
        },
        {
            "id": "item-updated-001",
            "name": "Updated Report.pdf",
            "file": {"mimeType": "application/pdf"},
            "webUrl": "https://contoso.sharepoint.com/sites/finance/Report.pdf",
            "parentReference": {
                "siteId": "site-001",
                "path": "/sites/finance/Shared Documents",
            },
            "cTag": "c:{updated-content}",
            "eTag": "e:{updated-meta}",
            "size": 98000,
        },
        {
            "id": "item-deleted-001",
            "deleted": {"state": "deleted"},
        },
    ]


@pytest.fixture
def sample_embeddings() -> list[list[float]]:
    """Sample embedding vectors (1536 dimensions, normalized)."""
    import numpy as np

    rng = np.random.default_rng(42)
    embeddings = []
    for _ in range(3):
        vec = rng.standard_normal(1536).astype(float)
        vec = (vec / np.linalg.norm(vec)).tolist()
        embeddings.append(vec)
    return embeddings


# ── Mock Fixtures ──

@pytest.fixture
def mock_graph_client() -> MagicMock:
    """Mock Microsoft Graph client."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.get_download_url = AsyncMock(return_value="https://download.url/file")
    client.download_file = AsyncMock(return_value=b"fake file content")
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_embedder() -> MagicMock:
    """Mock embedding provider."""
    import numpy as np

    embedder = MagicMock()
    rng = np.random.default_rng(42)

    async def mock_embed_query(text):
        vec = rng.standard_normal(1536).astype(float)
        return (vec / np.linalg.norm(vec)).tolist()

    async def mock_embed_texts(texts):
        result = []
        for _ in texts:
            vec = rng.standard_normal(1536).astype(float)
            result.append((vec / np.linalg.norm(vec)).tolist())
        return result

    embedder.embed_query = mock_embed_query
    embedder.embed_texts = mock_embed_texts
    embedder.dimensions = 1536
    return embedder
