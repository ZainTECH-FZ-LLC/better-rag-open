"""CC change handler — routes SharePoint delta changes to CCIngestionPipeline."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.change_handlers import _extract_user, _get_file_type
from src.connectors.graph_client import GraphClient
from src.customer_care.models import CCDocument
from src.models.enums import ChangeType, ProcessingStatus
from src.models.sync import SyncEvent

logger = structlog.get_logger()


class CCChangeHandler:
    """
    Routes classified SyncEvents to CCIngestionPipeline tasks for the CC KB.

    Mirrors ChangeHandler (src/connectors/change_handlers.py) but:
    - Creates / updates CCDocument records (not Document)
    - Dispatches run_cc_ingestion_task (not run_ingestion_task)
    """

    def __init__(self, db: AsyncSession, graph: GraphClient):
        self.db = db
        self.graph = graph

    async def process_changes(self, changes: list[SyncEvent]) -> dict[str, int]:
        """Process a batch of classified CC changes and dispatch Celery tasks."""
        stats = {"created": 0, "updated": 0, "deleted": 0, "permission": 0}

        for change in changes:
            try:
                if change.change_type == ChangeType.CREATED:
                    await self._handle_create(change)
                    stats["created"] += 1
                elif change.change_type == ChangeType.UPDATED:
                    await self._handle_update(change)
                    stats["updated"] += 1
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
                    "cc_change_handler.failed",
                    change_type=change.change_type,
                    item_id=change.drive_item_id,
                    error=str(e),
                )

        await self.db.flush()
        logger.info("cc_change_handler.batch_complete", **stats)
        return stats

    async def _handle_create(self, event: SyncEvent) -> None:
        """New CC file — create CCDocument record and dispatch CC ingestion task."""
        from src.celery_app import run_cc_ingestion_task

        item = event.raw_delta_item
        parent_ref = item.get("parentReference", {})

        doc = CCDocument(
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
            last_modified_graph=item.get("lastModifiedDateTime"),
            created_by=_extract_user(item.get("createdBy")),
            modified_by=_extract_user(item.get("lastModifiedBy")),
            status=ProcessingStatus.PENDING,
        )
        self.db.add(doc)
        await self.db.flush()

        task = run_cc_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "create",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("cc_change_handler.create", doc_id=str(doc.id), name=doc.name)

    async def _handle_update(self, event: SyncEvent) -> None:
        """CC file content changed — update metadata and re-process."""
        from src.celery_app import run_cc_ingestion_task

        item = event.raw_delta_item
        doc = await self._find_document(event.drive_id, event.drive_item_id)

        if doc is None:
            logger.warn("cc_change_handler.update_as_create", item_id=event.drive_item_id)
            event.change_type = ChangeType.CREATED
            await self._handle_create(event)
            return

        doc.name = item.get("name", doc.name)
        doc.size_bytes = item.get("size", doc.size_bytes)
        doc.ctag = item.get("cTag", doc.ctag)
        doc.etag = item.get("eTag", doc.etag)
        doc.last_modified_graph = item.get("lastModifiedDateTime", doc.last_modified_graph)
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by
        doc.sharepoint_url = item.get("webUrl", doc.sharepoint_url)
        doc.status = ProcessingStatus.PENDING
        doc.error_message = None
        doc.retry_count = 0

        task = run_cc_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "update",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("cc_change_handler.update", doc_id=str(doc.id), name=doc.name)

    async def _handle_delete(self, event: SyncEvent) -> None:
        """CC file deleted — remove all indexed artifacts."""
        from src.celery_app import run_cc_ingestion_task

        doc = await self._find_document(event.drive_id, event.drive_item_id)
        if doc is None:
            logger.debug("cc_change_handler.delete_not_found", item_id=event.drive_item_id)
            return

        doc.status = ProcessingStatus.DELETING

        task = run_cc_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "delete",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("cc_change_handler.delete", doc_id=str(doc.id), name=doc.name)

    async def _handle_permission_change(self, event: SyncEvent) -> None:
        """Only CC permissions changed — lightweight RBAC refresh."""
        from src.celery_app import run_cc_ingestion_task

        doc = await self._find_document(event.drive_id, event.drive_item_id)
        if doc is None:
            return

        item = event.raw_delta_item
        doc.etag = item.get("eTag", doc.etag)
        doc.modified_by = _extract_user(item.get("lastModifiedBy")) or doc.modified_by

        task = run_cc_ingestion_task.apply_async(
            kwargs={
                "document_id": str(doc.id),
                "drive_id": event.drive_id,
                "drive_item_id": event.drive_item_id,
                "operation": "refresh_permissions",
            },
            queue="rag.ingestion",
        )
        event.celery_task_id = task.id
        logger.info("cc_change_handler.permission_change", doc_id=str(doc.id))

    async def _find_document(
        self, drive_id: str, drive_item_id: str
    ) -> CCDocument | None:
        result = await self.db.execute(
            select(CCDocument).where(
                CCDocument.drive_id == drive_id,
                CCDocument.drive_item_id == drive_item_id,
                CCDocument.status != ProcessingStatus.DELETED,
            )
        )
        return result.scalar_one_or_none()
