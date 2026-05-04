"""Add extraction_method column to documents and document_chunks
for side-by-side OCR comparison testing.

Revision ID: 003
Revises: 002
Create Date: 2026-03-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All operations use raw SQL for Azure PostgreSQL compatibility
    # and IF NOT EXISTS / IF EXISTS for idempotency (safe to re-run).

    # Add extraction_method to documents (server_default backfills existing rows)
    op.execute("""
        ALTER TABLE documents
        ADD COLUMN IF NOT EXISTS extraction_method VARCHAR(50) NOT NULL DEFAULT 'vision_llm'
    """)

    # Add extraction_method to document_chunks
    op.execute("""
        ALTER TABLE document_chunks
        ADD COLUMN IF NOT EXISTS extraction_method VARCHAR(50)
    """)

    # Backfill existing chunks
    op.execute("UPDATE document_chunks SET extraction_method = 'vision_llm' WHERE extraction_method IS NULL")

    # Drop old unique index if it still exists
    op.execute("DROP INDEX IF EXISTS ix_documents_drive_item")

    # Create new unique index including extraction_method
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_drive_item_method
        ON documents (drive_id, drive_item_id, extraction_method)
    """)

    # Index for chunk filtering by extraction method
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_chunks_extraction_method
        ON document_chunks (extraction_method)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_extraction_method")
    op.execute("DROP INDEX IF EXISTS ix_documents_drive_item_method")

    # Restore original unique index
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_drive_item
        ON documents (drive_id, drive_item_id)
    """)

    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS extraction_method")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS extraction_method")
