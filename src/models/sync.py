"""Sync state models for SharePoint delta sync and audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.enums import ChangeType
from src.storage.db import Base


class SyncCursor(Base):
    """Stores delta tokens per drive for incremental SharePoint sync."""

    __tablename__ = "sync_cursors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    site_id: Mapped[str] = mapped_column(String(512), nullable=False)
    drive_id: Mapped[str] = mapped_column(String(255), nullable=False)
    delta_token: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_obtained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_crawl_completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_processed: Mapped[int] = mapped_column(Integer, default=0)

    # Webhook subscription tracking
    webhook_subscription_id: Mapped[str | None] = mapped_column(String(255))
    webhook_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_sync_cursor_drive", "site_id", "drive_id", unique=True),
    )


class SyncEvent(Base):
    """Audit log of every detected CRUD change from SharePoint delta sync."""

    __tablename__ = "sync_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    drive_id: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    change_type: Mapped[ChangeType] = mapped_column(
        Enum(ChangeType, name="change_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    file_name: Mapped[str | None] = mapped_column(String(512))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_delta_item: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_sync_events_detected", "detected_at"),
    )
