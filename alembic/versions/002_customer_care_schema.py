"""Customer Care knowledge base schema — cc_documents, cc_document_chunks,
cc_document_permissions, cc_document_user_access.

Revision ID: 002
Revises: 001
Create Date: 2026-02-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── cc_documents ──
    op.create_table(
        "cc_documents",
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
            # Reuse the already-created processing_status enum
            sa.Enum(name="processing_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text),
        sa.Column("retry_count", sa.Integer, server_default="0"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True)),
        sa.Column("blob_path", sa.String(1024)),
        sa.Column("summary", sa.Text),
        sa.Column("language", sa.String(10), server_default="en"),
        # CC-specific
        sa.Column("category", sa.String(100)),      # last folder segment of parent_path
        sa.Column("policy_url", sa.Text),            # canonical SharePoint URL for this policy
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("modified_by", sa.String(255)),
    )
    op.create_index("ix_cc_documents_drive_id", "cc_documents", ["drive_id"])
    op.create_index("ix_cc_documents_drive_item_id", "cc_documents", ["drive_item_id"])
    op.create_index("ix_cc_documents_site_id", "cc_documents", ["site_id"])
    op.create_index(
        "ix_cc_documents_drive_item", "cc_documents", ["drive_id", "drive_item_id"], unique=True
    )
    op.create_index("ix_cc_documents_status", "cc_documents", ["status"])
    op.create_index("ix_cc_documents_category", "cc_documents", ["category"])

    # ── cc_document_chunks ──
    op.create_table(
        "cc_document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cc_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_with_context", sa.Text, nullable=False),
        sa.Column(
            "chunk_type",
            sa.Enum(name="chunk_type", create_type=False),
            server_default="text",
        ),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("page_numbers", postgresql.ARRAY(sa.Integer)),
        sa.Column("section_heading", sa.String(512)),
        sa.Column("token_count", sa.Integer),
        sa.Column("embedding", Vector(1536)),
        sa.Column("access_level", sa.String(50)),
        sa.Column("sharepoint_url", sa.Text),
        sa.Column("document_title", sa.String(512)),
        # CC-specific
        sa.Column("category", sa.String(100)),
        sa.Column("policy_url", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cc_chunks_document_id", "cc_document_chunks", ["document_id"])
    op.create_index(
        "ix_cc_chunks_category_access", "cc_document_chunks", ["category", "access_level"]
    )
    op.create_index("ix_cc_chunks_created_at", "cc_document_chunks", ["created_at"])

    # HNSW index for CC vector search
    op.execute(
        """
        CREATE INDEX ix_cc_chunks_embedding_cosine
        ON cc_document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 24, ef_construction = 200)
        """
    )

    # ── cc_document_permissions ──
    op.create_table(
        "cc_document_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cc_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
    )
    op.create_index(
        "ix_cc_doc_permissions_doc_id", "cc_document_permissions", ["document_id"]
    )
    op.create_index(
        "ix_cc_doc_permissions_principal",
        "cc_document_permissions",
        ["principal_type", "principal_id"],
    )

    # ── cc_document_user_access ──
    op.create_table(
        "cc_document_user_access",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cc_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(255), primary_key=True),
    )
    op.create_index("ix_cc_user_access_user", "cc_document_user_access", ["user_id"])


def downgrade() -> None:
    op.drop_table("cc_document_user_access")
    op.drop_table("cc_document_permissions")
    op.drop_table("cc_document_chunks")
    op.drop_table("cc_documents")
