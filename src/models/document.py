"""Document and Chunk SQLAlchemy models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import ChunkType, FileType, ProcessingStatus
from src.storage.db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # SharePoint identity
    drive_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    drive_item_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    # File metadata
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    sharepoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    parent_path: Mapped[str | None] = mapped_column(Text)

    # Content tracking
    ctag: Mapped[str | None] = mapped_column(String(512))
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified_graph: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))

    # Processing state
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status", values_callable=lambda x: [e.value for e in x]),
        default=ProcessingStatus.PENDING,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Extraction method (which OCR/vision approach produced the chunks)
    extraction_method: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="hybrid"
    )

    # Blob staging
    blob_path: Mapped[str | None] = mapped_column(String(1024))

    # LLM-derived metadata
    summary: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(String(50))
    content_type_tag: Mapped[str | None] = mapped_column(String(50))
    topics: Mapped[dict | None] = mapped_column(JSON)
    language: Mapped[str] = mapped_column(String(10), default="en")

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    modified_by: Mapped[str | None] = mapped_column(String(255))

    # Relationships
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    permissions: Mapped[list[DocumentPermission]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_documents_drive_item_method", "drive_id", "drive_item_id", "extraction_method", unique=True),
        Index("ix_documents_status", "status"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_with_context: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_type: Mapped[ChunkType] = mapped_column(
        Enum(ChunkType, name="chunk_type", values_callable=lambda x: [e.value for e in x]), default=ChunkType.TEXT
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_numbers: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    section_heading: Mapped[str | None] = mapped_column(String(512))
    token_count: Mapped[int | None] = mapped_column(Integer)

    # Embedding (1536 dimensions by default, configurable)
    embedding = mapped_column(Vector(1536), nullable=True)

    # Extraction method (denormalized from document for filter performance)
    extraction_method: Mapped[str | None] = mapped_column(String(50), index=True)

    # Denormalized for filter performance
    department: Mapped[str | None] = mapped_column(String(50), index=True)
    access_level: Mapped[str | None] = mapped_column(String(50))
    content_type: Mapped[str | None] = mapped_column(String(50))
    sharepoint_url: Mapped[str | None] = mapped_column(Text)
    document_title: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_dept_access", "department", "access_level"),
        Index("ix_chunks_created_at", "created_at"),
    )


class DocumentPermission(Base):
    __tablename__ = "document_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    principal_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user, group
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # read, write, owner

    document: Mapped[Document] = relationship(back_populates="permissions")

    __table_args__ = (
        Index("ix_doc_permissions_doc_id", "document_id"),
        Index("ix_doc_permissions_principal", "principal_type", "principal_id"),
    )


class DocumentUserAccess(Base):
    """Pre-expanded user access for fast query-time RBAC JOINs."""

    __tablename__ = "document_user_access"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    __table_args__ = (
        Index("ix_user_access_user", "user_id"),
    )
