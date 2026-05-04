"""Shared enumerations for the better-rag system."""

from enum import Enum


class FileType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class ChangeType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    PERMISSION_CHANGED = "permission_changed"


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE_DESCRIPTION = "image_description"
    SUMMARY = "summary"


class QueryType(str, Enum):
    FACTUAL = "factual"
    ANALYTICAL = "analytical"
    PROCEDURAL = "procedural"
    GENERATIVE = "generative"


class Department(str, Enum):
    HR = "hr"
    FINANCE = "finance"
    SALES = "sales"
    MARKETING = "marketing"
    GENERAL = "general"


class RetrievalStrategy(str, Enum):
    COSINE = "cosine"
    HYDE_COSINE = "hyde_cosine"
    MMR = "mmr"
