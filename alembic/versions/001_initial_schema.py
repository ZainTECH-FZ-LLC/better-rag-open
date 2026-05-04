"""Initial schema — documents, chunks, permissions, sync state.

Revision ID: 001
Revises: None
Create Date: 2026-02-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── documents ──
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("drive_id", sa.String(255), nullable=False),
        sa.Column("drive_item_id", sa.String(255), nullable=False),
        sa.Column("site_id", sa.String(512), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("sharepoint_url", sa.Text, nullable=False),
        sa.Column("parent_path", sa.Text),
        sa.Column("ctag", sa.String(512)),
        sa.Column("etag", sa.String(512)),
        sa.Column("last_modified_graph", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "downloading", "processing", "chunking",
                "embedding", "indexing", "completed", "failed",
                "deleting", "deleted",
                name="processing_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text),
        sa.Column("retry_count", sa.Integer, server_default="0"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True)),
        sa.Column("blob_path", sa.String(1024)),
        sa.Column("summary", sa.Text),
        sa.Column("department", sa.String(50)),
        sa.Column("content_type_tag", sa.String(50)),
        sa.Column("topics", postgresql.JSON),
        sa.Column("language", sa.String(10), server_default="en"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("modified_by", sa.String(255)),
    )
    op.create_index("ix_documents_drive_id", "documents", ["drive_id"])
    op.create_index("ix_documents_drive_item_id", "documents", ["drive_item_id"])
    op.create_index("ix_documents_site_id", "documents", ["site_id"])
    op.create_index(
        "ix_documents_drive_item", "documents", ["drive_id", "drive_item_id"], unique=True
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    # ── document_chunks ──
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_with_context", sa.Text, nullable=False),
        sa.Column(
            "chunk_type",
            sa.Enum("text", "table", "image_description", "summary", name="chunk_type"),
            server_default="text",
        ),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("page_numbers", postgresql.ARRAY(sa.Integer)),
        sa.Column("section_heading", sa.String(512)),
        sa.Column("token_count", sa.Integer),
        sa.Column("embedding", Vector(1536)),
        sa.Column("department", sa.String(50)),
        sa.Column("access_level", sa.String(50)),
        sa.Column("content_type", sa.String(50)),
        sa.Column("sharepoint_url", sa.Text),
        sa.Column("document_title", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_chunks_dept_access", "document_chunks", ["department", "access_level"])
    op.create_index("ix_chunks_created_at", "document_chunks", ["created_at"])

    # HNSW index for vector search (high recall config)
    op.execute(
        """
        CREATE INDEX ix_chunks_embedding_cosine
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 24, ef_construction = 200)
        """
    )

    # ── document_permissions ──
    op.create_table(
        "document_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
    )
    op.create_index("ix_doc_permissions_doc_id", "document_permissions", ["document_id"])
    op.create_index(
        "ix_doc_permissions_principal",
        "document_permissions",
        ["principal_type", "principal_id"],
    )

    # ── document_user_access (pre-expanded for fast RBAC JOINs) ──
    op.create_table(
        "document_user_access",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(255), primary_key=True),
    )
    op.create_index("ix_user_access_user", "document_user_access", ["user_id"])

    # ── sync_cursors ──
    op.create_table(
        "sync_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", sa.String(512), nullable=False),
        sa.Column("drive_id", sa.String(255), nullable=False),
        sa.Column("delta_token", sa.Text),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("token_obtained_at", sa.DateTime(timezone=True)),
        sa.Column("full_crawl_completed", sa.DateTime(timezone=True)),
        sa.Column("items_processed", sa.Integer, server_default="0"),
        sa.Column("webhook_subscription_id", sa.String(255)),
        sa.Column("webhook_expiry", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_sync_cursor_drive", "sync_cursors", ["site_id", "drive_id"], unique=True
    )

    # ── sync_events ──
    op.create_table(
        "sync_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("drive_id", sa.String(255), nullable=False),
        sa.Column("drive_item_id", sa.String(255), nullable=False),
        sa.Column(
            "change_type",
            sa.Enum("created", "updated", "deleted", "permission_changed", name="change_type"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(512)),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("error_message", sa.Text),
        sa.Column("raw_delta_item", postgresql.JSON),
    )
    op.create_index("ix_sync_events_detected", "sync_events", ["detected_at"])


def downgrade() -> None:
    op.drop_table("sync_events")
    op.drop_table("sync_cursors")
    op.drop_table("document_user_access")
    op.drop_table("document_permissions")
    op.drop_table("document_chunks")
    op.drop_table("documents")

    op.execute("DROP TYPE IF EXISTS processing_status")
    op.execute("DROP TYPE IF EXISTS chunk_type")
    op.execute("DROP TYPE IF EXISTS change_type")
