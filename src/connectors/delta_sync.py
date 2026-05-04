"""Delta Sync Manager — polls Microsoft Graph delta API for SharePoint CRUD changes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.connectors.graph_client import (
    GraphClient,
    GraphNotFoundError,
    GraphTokenExpiredError,
)
from src.models.document import Document
from src.models.enums import ChangeType, ProcessingStatus
from src.models.sync import SyncCursor, SyncEvent

logger = structlog.get_logger()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}

DELTA_SELECT_FIELDS = (
    "id,name,file,folder,deleted,parentReference,"
    "lastModifiedDateTime,lastModifiedBy,createdBy,"
    "webUrl,cTag,eTag,size"
)


class DeltaSyncManager:
    """
    Polls Microsoft Graph delta API to detect CRUD changes in SharePoint drives.

    Usage:
        manager = DeltaSyncManager(graph_client, db_session)
        changes = await manager.sync_drive(site_id, drive_id)
    """

    def __init__(self, graph: GraphClient, db: AsyncSession):
        self.graph = graph
        self.db = db
        self.settings = get_settings()

    async def sync_drive(self, site_id: str, drive_id: str) -> list[SyncEvent]:
        """
        Run a delta sync for a single drive.

        1. Load existing delta_token (or None for initial crawl)
        2. Call delta API, paginate through all pages
        3. Classify each driveItem as Create/Update/Delete
        4. Persist SyncEvents
        5. Save new delta_token
        """
        cursor = await self._get_or_create_cursor(site_id, drive_id)
        delta_url = cursor.delta_token

        all_changes: list[SyncEvent] = []
        total_items = 0
        page_count = 0

        try:
            # Determine whether to use existing delta_token or start fresh
            if delta_url is None:
                logger.info("delta_sync.initial_crawl", drive_id=drive_id)
                response = await self.graph.get(
                    f"/drives/{drive_id}/root/delta",
                    params={"$select": DELTA_SELECT_FIELDS},
                )
            else:
                # Check token age — force re-crawl if near expiry
                if cursor.token_obtained_at:
                    age = datetime.now(timezone.utc) - cursor.token_obtained_at
                    if age > timedelta(days=self.settings.DELTA_TOKEN_MAX_AGE_DAYS):
                        logger.warn(
                            "delta_sync.token_expiring",
                            drive_id=drive_id,
                            age_days=age.days,
                        )
                        # Reset to full crawl
                        delta_url = None
                        response = await self.graph.get(
                            f"/drives/{drive_id}/root/delta",
                            params={"$select": DELTA_SELECT_FIELDS},
                        )
                    else:
                        response = await self.graph.get(delta_url)
                else:
                    response = await self.graph.get(delta_url)

            # Paginate through all pages
            while True:
                page_count += 1
                data = response.json()
                items = data.get("value", [])
                total_items += len(items)

                logger.debug(
                    "delta_sync.page",
                    drive_id=drive_id,
                    page=page_count,
                    items=len(items),
                )

                for item in items:
                    change = await self._classify_and_record(drive_id, item)
                    if change:
                        all_changes.append(change)

                next_link = data.get("@odata.nextLink")
                delta_link = data.get("@odata.deltaLink")

                if next_link:
                    response = await self.graph.get(next_link)
                elif delta_link:
                    cursor.delta_token = delta_link
                    cursor.last_sync_at = datetime.now(timezone.utc)
                    cursor.token_obtained_at = datetime.now(timezone.utc)
                    cursor.items_processed += total_items
                    if delta_url is None:
                        cursor.full_crawl_completed = datetime.now(timezone.utc)
                    break
                else:
                    logger.error("delta_sync.no_link", drive_id=drive_id)
                    break

            await self.db.flush()
            logger.info(
                "delta_sync.completed",
                drive_id=drive_id,
                pages=page_count,
                changes=len(all_changes),
                total_items=total_items,
            )

        except GraphTokenExpiredError:
            # HTTP 410 — delta token expired, reset for full re-crawl
            logger.warn("delta_sync.token_expired", drive_id=drive_id)
            cursor.delta_token = None
            await self.db.flush()
            raise

        except GraphNotFoundError:
            # HTTP 404 — drive may have been deleted or moved
            logger.error(
                "delta_sync.drive_not_found",
                drive_id=drive_id,
                site_id=site_id,
            )
            raise

        except Exception as e:
            logger.error("delta_sync.failed", drive_id=drive_id, error=str(e))
            raise

        return all_changes

    async def get_all_drive_item_ids(self, drive_id: str) -> set[str]:
        """
        Enumerate ALL item IDs currently in a SharePoint drive.

        Used by full reconciliation to detect orphan documents
        (items in our DB that no longer exist in SharePoint).

        Uses the children endpoint recursively since delta queries
        may not return items that haven't changed.
        """
        all_ids: set[str] = set()
        await self._enumerate_folder(drive_id, "root", all_ids)
        return all_ids

    async def _enumerate_folder(
        self, drive_id: str, folder_id: str, result_ids: set[str]
    ) -> None:
        """Recursively enumerate items in a folder."""
        url = f"/drives/{drive_id}/items/{folder_id}/children"
        params = {"$select": "id,name,file,folder", "$top": "200"}

        while url:
            response = await self.graph.get(url, params=params)
            data = response.json()

            for item in data.get("value", []):
                if "file" in item:
                    ext = _get_extension(item.get("name", ""))
                    if ext in SUPPORTED_EXTENSIONS:
                        result_ids.add(item["id"])
                elif "folder" in item:
                    # Recurse into subfolders
                    await self._enumerate_folder(drive_id, item["id"], result_ids)

            url = data.get("@odata.nextLink")
            params = None

    async def _classify_and_record(
        self, drive_id: str, item: dict
    ) -> SyncEvent | None:
        """
        Classify a driveItem from delta response as Create, Update, or Delete.

        Classification:
        - deleted facet → DELETE
        - folder facet → SKIP
        - file facet + not in DB → CREATE
        - file facet + in DB + cTag changed → UPDATE (content changed)
        - file facet + in DB + cTag same + eTag changed → PERMISSION_CHANGED
        - Unsupported extension → SKIP
        """
        item_id = item.get("id")
        name = item.get("name", "")

        # DELETE
        if "deleted" in item:
            existing = await self._find_document(drive_id, item_id)
            if existing:
                event = SyncEvent(
                    drive_id=drive_id,
                    drive_item_id=item_id,
                    change_type=ChangeType.DELETED,
                    file_name=existing.name,
                    raw_delta_item=item,
                )
                self.db.add(event)
                return event
            return None

        # Skip folders and non-file items
        if "folder" in item or "file" not in item:
            return None

        # Skip unsupported extensions
        ext = _get_extension(name)
        if ext not in SUPPORTED_EXTENSIONS:
            return None

        # CREATE or UPDATE
        existing = await self._find_document(drive_id, item_id)

        if existing is None:
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.CREATED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        new_ctag = item.get("cTag")
        new_etag = item.get("eTag")

        if new_ctag and new_ctag != existing.ctag:
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.UPDATED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        if new_etag and new_etag != existing.etag:
            event = SyncEvent(
                drive_id=drive_id,
                drive_item_id=item_id,
                change_type=ChangeType.PERMISSION_CHANGED,
                file_name=name,
                raw_delta_item=item,
            )
            self.db.add(event)
            return event

        return None

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

    async def _get_or_create_cursor(
        self, site_id: str, drive_id: str
    ) -> SyncCursor:
        result = await self.db.execute(
            select(SyncCursor).where(
                SyncCursor.site_id == site_id,
                SyncCursor.drive_id == drive_id,
            )
        )
        cursor = result.scalar_one_or_none()
        if cursor is None:
            cursor = SyncCursor(site_id=site_id, drive_id=drive_id)
            self.db.add(cursor)
            await self.db.flush()
        return cursor


def _get_extension(filename: str) -> str:
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""
