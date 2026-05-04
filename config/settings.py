"""
Centralized configuration via Pydantic Settings.

Config is loaded from (highest → lowest priority):
1. Mounted secret files at /mnt/secrets/<FIELD_NAME> (Kubernetes-style)
2. Environment variables
3. .env file
"""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import os

_use_secrets = os.getenv("USE_MOUNTED_SECRETS", "auto").lower()
if _use_secrets == "false":
    SECRETS_DIR: Path | None = None
elif _use_secrets == "true":
    SECRETS_DIR = Path("/mnt/secrets")
else:  # "auto" — use only if the directory exists
    _candidate = Path("/mnt/secrets")
    SECRETS_DIR = _candidate if _candidate.is_dir() else None


class LLMProvider(str, Enum):
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"


class OCRProvider(str, Enum):
    AZURE_DI = "azure_di"
    DOCTR = "doctr"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        secrets_dir=str(SECRETS_DIR) if SECRETS_DIR else None,
    )

    # ── Application ──
    APP_NAME: str = "better-rag"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── LLM Provider ──
    LLM_PROVIDER: LLMProvider = LLMProvider.AZURE_OPENAI
    LLM_EXPENSIVE_MODEL: str = "gpt-4o"
    LLM_CHEAP_MODEL: str = "gpt-4o-mini"

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # LLM Rate Limits
    LLM_RATE_LIMIT_RPM: int = 500
    LLM_RATE_LIMIT_TPM: int = 150000
    LLM_CONCURRENT_REQUESTS: int = 50

    # ── Microsoft Graph / SharePoint ──
    GRAPH_TENANT_ID: str = ""
    GRAPH_CLIENT_ID: str = ""
    GRAPH_CLIENT_SECRET: str = ""
    GRAPH_SITE_URLS: str = "[]"  # JSON list of SharePoint site URLs

    # Drives to monitor (JSON list of {"site_id": ..., "drive_id": ...})
    SHAREPOINT_DRIVES: str = "[]"

    # ── Customer Care KB ──
    # Separate SharePoint drives for the CC knowledge base
    CC_SHAREPOINT_DRIVES: str = "[]"
    # Brand voice / tone text injected into the CC agent system prompt
    CC_BRAND_GUIDELINES: str = ""
    # JSON list of {name, trigger_keywords, pitch_template} for upsell assessment
    CC_UPSELL_PRODUCTS: str = "[]"

    @field_validator(
        "SHAREPOINT_DRIVES", "GRAPH_SITE_URLS",
        "CC_SHAREPOINT_DRIVES", "CC_UPSELL_PRODUCTS",
        mode="before",
    )
    @classmethod
    def parse_json_list(cls, v: str) -> str:
        if isinstance(v, list):
            return json.dumps(v)
        return v

    def get_sharepoint_drives(self) -> list[dict]:
        return json.loads(self.SHAREPOINT_DRIVES)

    def get_graph_site_urls(self) -> list[str]:
        return json.loads(self.GRAPH_SITE_URLS)

    def get_cc_sharepoint_drives(self) -> list[dict]:
        return json.loads(self.CC_SHAREPOINT_DRIVES)

    def get_cc_upsell_products(self) -> list[dict]:
        return json.loads(self.CC_UPSELL_PRODUCTS)

    # ── Azure Blob Storage ──
    BLOB_ACCOUNT_URL: str = ""
    BLOB_ACCOUNT_KEY: str = ""
    BLOB_CONTAINER_NAME: str = "documents"

    # ── OCR ──
    OCR_PROVIDER: OCRProvider = OCRProvider.AZURE_DI
    OCR_AZURE_ENDPOINT: str = ""
    OCR_AZURE_KEY: str = ""

    # ── Vision (for slide chart/graph interpretation) ──
    VISION_AZURE_ENDPOINT: str = ""
    VISION_AZURE_API_KEY: str = ""
    VISION_AZURE_DEPLOYMENT: str = "gpt-4.1-mini"
    VISION_AZURE_API_VERSION: str = "2025-01-01-preview"

    # ── Mistral OCR (Document AI 2512 via Azure AI Foundry) ──
    MISTRAL_OCR_ENDPOINT: str = ""   # e.g. https://<resource>.services.ai.azure.com/providers/mistral/azure/ocr
    MISTRAL_OCR_API_KEY: str = ""
    MISTRAL_OCR_MODEL: str = "mistral-document-ai-2512"

    # ── Embedding ──
    EMBEDDING_AZURE_ENDPOINT: str = ""
    EMBEDDING_AZURE_API_KEY: str = ""
    EMBEDDING_AZURE_DEPLOYMENT: str = "text-embedding-3-large"
    EMBEDDING_AZURE_API_VERSION: str = "2023-05-15"
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_BATCH_SIZE: int = 16

    # ── Reranker (Cohere via Azure AI Foundry) ──
    COHERE_AZURE_ENDPOINT: str = ""   # e.g. https://<resource>.services.ai.azure.com/providers/cohere/v2
    COHERE_AZURE_API_KEY: str = ""
    COHERE_RERANK_MODEL: str = "cohere-rerank-v4.0-fast"

    # ── Chunking ──
    CHUNK_TARGET_TOKENS: int = 450
    CHUNK_MAX_TOKENS: int = 600
    CHUNK_OVERLAP_TOKENS: int = 60
    CHUNK_ENABLE_SEMANTIC_BOUNDARY: bool = False

    # ── PostgreSQL + pgvector ──
    PGVECTOR_HOST: str = "localhost"
    PGVECTOR_PORT: int = 5432
    PGVECTOR_DATABASE: str = "betterrag"
    PGVECTOR_USER: str = "betterrag"
    PGVECTOR_PASSWORD: str = ""
    PGVECTOR_POOL_SIZE: int = 20
    PGVECTOR_POOL_MAX_OVERFLOW: int = 10
    PGVECTOR_HNSW_M: int = 24
    PGVECTOR_HNSW_EF_CONSTRUCTION: int = 200
    PGVECTOR_HNSW_EF_SEARCH: int = 200

    @property
    def pgvector_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.PGVECTOR_USER}:{self.PGVECTOR_PASSWORD}"
            f"@{self.PGVECTOR_HOST}:{self.PGVECTOR_PORT}/{self.PGVECTOR_DATABASE}"
        )

    @property
    def pgvector_dsn_sync(self) -> str:
        return (
            f"postgresql://{self.PGVECTOR_USER}:{self.PGVECTOR_PASSWORD}"
            f"@{self.PGVECTOR_HOST}:{self.PGVECTOR_PORT}/{self.PGVECTOR_DATABASE}"
        )

    # ── Neo4j ──
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = 50
    NEO4J_CONNECTION_ACQUISITION_TIMEOUT: int = 30

    # ── Redis ──
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_SSL: bool = False

    @property
    def _redis_scheme(self) -> str:
        return "rediss" if self.REDIS_SSL else "redis"

    @property
    def _redis_auth(self) -> str:
        return f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""

    def redis_url(self, db: int = 0) -> str:
        base = f"{self._redis_scheme}://{self._redis_auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{db}"
        if self.REDIS_SSL:
            base += "?ssl_cert_reqs=required"
        return base

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url(db=1)

    @property
    def redis_streams_url(self) -> str:
        return self.redis_url(db=2)

    @property
    def redis_cache_url(self) -> str:
        return self.redis_url(db=3)

    @property
    def langgraph_checkpoint_url(self) -> str:
        return self.redis_url(db=4)

    # ── Cache TTLs ──
    CACHE_HYDE_TTL: int = 3600
    CACHE_RBAC_TTL: int = 300
    CACHE_QUERY_TTL: int = 120

    # ── Celery Worker Tuning ──
    QUERY_WORKER_CONCURRENCY: int = 10
    DOCGEN_WORKER_CONCURRENCY: int = 2
    INGESTION_WORKER_CONCURRENCY: int = 4

    # ── Poller / Delta Sync ──
    DELTA_SYNC_INTERVAL_MINUTES: int = 15
    BEAT_ENABLED: bool = True  # False = disable scheduled tasks (manual trigger only)
    DELTA_TOKEN_MAX_AGE_DAYS: int = 25
    INGESTION_MAX_RETRIES: int = 3
    INGESTION_MAX_FILES: int = 0  # 0 = unlimited, >0 = limit per delta sync cycle
    INGESTION_PATH_FILTER: str = ""  # Only ingest files under this path (empty = all)
    INGESTION_PATH_EXCLUDE: list[str] = []  # Exclude files whose path contains any of these strings
    INGESTION_RETRY_DELAY_SECONDS: int = 60
    SUPPORTED_FILE_EXTENSIONS: list[str] = [".pdf", ".docx", ".pptx", ".xlsx"]

    # ── Webhook ──
    PUBLIC_BASE_URL: str = "https://localhost:8000"
    WEBHOOK_CLIENT_STATE: str = ""

    # ── API Auth ──
    BETTER_RAG_API_KEY: str = ""

    # ── Open WebUI ──
    OAUTH_CLIENT_ID: str = ""
    OAUTH_CLIENT_SECRET: str = ""

    # ── Paths ──
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    GENERATED_DIR: Path = Field(default=None)
    TEMPLATES_DIR: Path = Field(default=None)
    SKILLS_DIR: Path = Field(default=None)

    @field_validator("GENERATED_DIR", mode="before")
    @classmethod
    def set_generated_dir(cls, v: Path | None) -> Path:
        if v is not None:
            return Path(v)
        return Path(__file__).resolve().parent.parent / "generated"

    @field_validator("TEMPLATES_DIR", mode="before")
    @classmethod
    def set_templates_dir(cls, v: Path | None) -> Path:
        if v is not None:
            return Path(v)
        return Path(__file__).resolve().parent.parent / "src" / "templates"

    @field_validator("SKILLS_DIR", mode="before")
    @classmethod
    def set_skills_dir(cls, v: Path | None) -> Path:
        if v is not None:
            return Path(v)
        return Path(__file__).resolve().parent.parent / "src" / "skills"


@lru_cache
def get_settings() -> Settings:
    return Settings()
