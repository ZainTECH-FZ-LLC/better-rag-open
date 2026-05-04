"""CRUD change handlers — routes SharePoint delta changes to appropriate pipelines."""

from __future__ import annotations

from datetime import datetime, timezone
from dateutil.parser import isoparse

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.graph_client import GraphClient
from src.models.document import Document
from src.models.enums import ChangeType, ProcessingStatus
from src.models.sync import SyncEvent

logger = structlog.get_logger()


class ChangeHandler:
    """Routes classified SyncEvents to the appropriate CRUD handler."""

    def __init__(self, db: AsyncSession, graph: GraphClient):
        self.db = db
        self.graph = graph

    async def process_changes(
        self,
        changes: list[SyncEvent],
        *,
        max_files: int = 0,
        path_filter: str = "",
        path_exclude: list[str] | None = None,
    ) -> dict[str, int]:
        """Process a batch of classified changes and dispatch Celery tasks.

        Args:
            max_files: If > 0, only dispatch ingestion for the first N create/update files.
                       Deletes and permission changes are always processed.
            path_filter: If set, only process files whose parent path contains this string.
            path_exclude: List of path substrings to exclude (checked after path_filter).
        """
        stats = {"created": 0, "updated": 0, "deleted": 0, "permission": 0, "skipped": 0}
        ingested = 0
        exclude_lower = [p.lower() for p in (path_exclude or [])]

        for change in changes:
            # Path filter — skip files outside the target folder or in excluded paths
            if change.change_type in (ChangeType.CREATED, ChangeType.UPDATED):
                parent_path = change.raw_delta_item.get("parentReference", {}).get("path", "")
                path_lower = parent_path.lower()

                if path_filter and path_filter.lower() not in path_lower:
                    stats["skipped"] += 1
                    continue

                if any(excl in path_lower for excl in exclude_lower):
                    stats["skipped"] += 1
                    continue

            if max_files > 0 and change.change_type in (ChangeType.CREATED, ChangeType.UPDATED):
                if ingested >= max_files:
                    stats["skipped"] += 1
                    continue
            try:
                if change.change_type == ChangeType.CREATED:
                    await self._handle_create(change)
                    stats["created"] += 1
                    ingested += 1
                elif change.change_type == ChangeType.UPDATED:
                    await self._handle_update(change)
                    stats["updated"] += 1
                    ingested += 1
                elif change.change_type == ChangeType.DELETED:
                    await self._handle_delete(change)
                    stats["deleted"] += 1
                elif change.change_type == ChangeType.PERMISSION_CHANGED:
                    await self._handle_permission_change(change)
                    stats["permission"] += 1

                change.processed_at = datetime.now(timezone.utc)

            except Exception as e:
                change.error_message = str(e)[:1000]
                logger.error(
                    "change_handler.failed",
                    change_type=change.change_type,
                    item_id=change.drive_item_id,
                    error=str(e),
                )

        await self.db.flush()
        logger.info("change_handler.batch_complete", **stats)
        return stats

    async def _handle_create(self, event: SyncEvent) -> None:
        """New file — create Document record and dispatch ingestion task."""
        from src.celery_app import run_ingestion_task

        item = event.raw_delta_item
        parent_ref = item.get("parentReference", {})

        doc = Document(
            drive_id=event.drive_id,
            drive_item_id=event.drive_item_id,
            site_id=parent_ref.get("siteId", ""),
            name=item.get("name", ""),
            file_type=_get_file_type(item.get("name", "")),
            size_bytes=item.get("size"),
            mime_type=item.get("file", {}).get("mimeType"),
            sharepoint_url=item.get("webUrl", ""),
            parent_path=parent_ref.get("path", ""),
            ctag=item.get("cTag"),
            etag=item.get("eTag"),
            last_modified_graph=_parse_datetime(item.get("lastModifiedDateTime")),
            created_by=_extract_user(item.get("createdBy")),
            modified_by=_extract_user(item.get("lastModifiedBy")),
            status=ProcessingStatus.PENDING,
        )
        self.db.add(doc)
        await self.db.flush()

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "create",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("change_handler.create", doc_id=str(doc.id), name=doc.name)

    async def _handle_update(self, event: SyncEvent) -> None:
        """File content changed — update metadata and re-process."""
        from src.celery_app import run_ingestion_task

        item = event.raw_delta_item
        doc = await self._find_document(event.drive_id, event.drive_item_id)

        if doc is None:
            logger.warn("change_handler.update_as_create", item_id=event.drive_item_id)
            event.change_type = ChangeType.CREATED
            await self._handle_create(event)
            return

        doc.name = item.get("name", doc.name)
        doc.size_bytes = item.get("size", doc.size_bytes)
        doc.ctag = item.get("cTag", doc.ctag)
        doc.etag = item.get("eTag", doc.etag)
        doc.last_modified_graph = _parse_datetime(item.get("lastModifiedDateTime")) or doc.last_modified_graph
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by
        doc.sharepoint_url = item.get("webUrl", doc.sharepoint_url)
        doc.status = ProcessingStatus.PENDING
        doc.error_message = None
        doc.retry_count = 0

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "update",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("change_handler.update", doc_id=str(doc.id), name=doc.name)

    async def _handle_delete(self, event: SyncEvent) -> None:
        """File deleted — remove all indexed artifacts."""
        from src.celery_app import run_ingestion_task

        doc = await self._find_document(event.drive_id, event.drive_item_id)
        if doc is None:
            logger.debug("change_handler.delete_not_found", item_id=event.drive_item_id)
            return

        doc.status = ProcessingStatus.DELETING

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "delete",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("change_handler.delete", doc_id=str(doc.id), name=doc.name)

    async def _handle_permission_change(self, event: SyncEvent) -> None:
        """Only permissions changed — lightweight RBAC refresh."""
        from src.celery_app import run_ingestion_task

        doc = await self._find_document(event.drive_id, event.drive_item_id)
        if doc is None:
            return

        item = event.raw_delta_item
        doc.etag = item.get("eTag", doc.etag)
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by

        task = run_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "refresh_permissions",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("change_handler.permission_change", doc_id=str(doc.id))

    async def _find_document(
        self, drive_id: str, drive_item_id: str
    ) -> Document | None:
        result = await self.db.execute(
            select(Document).where(
                Document.drive_id == drive_id,
                Document.drive_item_id == drive_item_id,
                Document.status != ProcessingStatus.DELETED,
            )
        )
        return result.scalar_one_or_none()


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse ISO datetime strings from Graph API into Python datetime objects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return isoparse(value)
    except (ValueError, TypeError):
        return None


def _extract_user(user_dict: dict | None) -> str | None:
    if not user_dict:
        return None
    user = user_dict.get("user", {})
    return user.get("email") or user.get("displayName")


def _get_file_type(filename: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""
