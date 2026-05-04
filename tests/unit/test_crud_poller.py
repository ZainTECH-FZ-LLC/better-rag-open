"""Unit tests for the full CRUD poller flow: delta sync → classify → handle."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.change_handlers import ChangeHandler
from src.connectors.delta_sync import DeltaSyncManager
from src.connectors.graph_client import GraphTokenExpiredError
from src.models.document import Document
from src.models.enums import ChangeType, ProcessingStatus
from src.models.sync import SyncCursor, SyncEvent


# ── Helpers ──

def _make_drive_item(
    item_id: str,
    name: str = "test.pdf",
    ctag: str = "c:{v1}",
    etag: str = "e:{v1}",
    deleted: bool = False,
    is_folder: bool = False,
) -> dict:
    """Create a Graph delta driveItem dict."""
    item: dict = {
        "id": item_id,
        "name": name,
        "webUrl": f"https://contoso.sharepoint.com/{name}",
        "parentReference": {
            "siteId": "site-001",
            "path": "/sites/test/Shared Documents",
        },
        "cTag": ctag,
        "eTag": etag,
        "size": 10000,
        "lastModifiedDateTime": "2026-02-22T10:00:00Z",
        "createdBy": {"user": {"email": "admin@contoso.com"}},
        "lastModifiedBy": {"user": {"email": "admin@contoso.com"}},
    }

    if deleted:
        item["deleted"] = {"state": "deleted"}
    elif is_folder:
        item["folder"] = {"childCount": 5}
    else:
        item["file"] = {"mimeType": "application/pdf"}

    return item


def _make_delta_response(items: list[dict], delta_link: str | None = None, next_link: str | None = None) -> dict:
    """Create a mock Graph delta API response."""
    resp: dict = {"value": items}
    if delta_link:
        resp["@odata.deltaLink"] = delta_link
    if next_link:
        resp["@odata.nextLink"] = next_link
    return resp


# ── Fixtures ──

@pytest.fixture
def mock_db():
    """Mock async SQLAlchemy session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_graph():
    """Mock Graph client."""
    graph = MagicMock()
    graph.get = AsyncMock()
    graph.post = AsyncMock()
    graph.patch = AsyncMock()
    graph.close = AsyncMock()
    graph.get_item_permissions = AsyncMock(return_value=[])
    graph.get_transitive_members = AsyncMock(return_value=[])
    return graph


# ── DeltaSyncManager Tests ──

class TestDeltaSyncClassification:
    """Tests for driveItem classification logic."""

    @pytest.mark.asyncio
    async def test_new_file_classified_as_created(self, mock_db, mock_graph):
        """A new file not in our DB should be classified as CREATED."""
        # Mock: no existing document found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock: no existing cursor, create new one
        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.side_effect = [cursor, None]  # cursor, then doc lookup
        mock_db.execute.side_effect = [
            mock_cursor_result,  # _get_or_create_cursor
            mock_result,         # _find_document for new item
        ]

        item = _make_drive_item("item-new", "report.pdf")
        response_data = _make_delta_response([item], delta_link="https://delta.link/token")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        # Should have one CREATED event
        added_items = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_items) == 1
        assert added_items[0].change_type == ChangeType.CREATED
        assert added_items[0].file_name == "report.pdf"

    @pytest.mark.asyncio
    async def test_deleted_item_classified_correctly(self, mock_db, mock_graph):
        """An item with 'deleted' facet should be classified as DELETED if we track it."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-del",
            name="old-report.pdf",
            site_id="site-001",
            file_type="pdf",
            sharepoint_url="https://contoso.sharepoint.com/old-report.pdf",
            status=ProcessingStatus.COMPLETED,
        )

        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")

        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        mock_doc_result = MagicMock()
        mock_doc_result.scalar_one_or_none.return_value = existing_doc

        mock_db.execute.side_effect = [mock_cursor_result, mock_doc_result]

        item = _make_drive_item("item-del", deleted=True)
        response_data = _make_delta_response([item], delta_link="https://delta.link/token2")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        added_events = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_events) == 1
        assert added_events[0].change_type == ChangeType.DELETED
        assert added_events[0].file_name == "old-report.pdf"

    @pytest.mark.asyncio
    async def test_folder_skipped(self, mock_db, mock_graph):
        """Folders should be silently skipped."""
        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        mock_db.execute.return_value = mock_cursor_result

        item = _make_drive_item("folder-001", "Documents", is_folder=True)
        response_data = _make_delta_response([item], delta_link="https://delta.link/token3")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        # No SyncEvents should be added
        added_events = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_events) == 0

    @pytest.mark.asyncio
    async def test_unsupported_extension_skipped(self, mock_db, mock_graph):
        """Files with unsupported extensions (.mp4, .zip, etc.) should be skipped."""
        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        mock_db.execute.return_value = mock_cursor_result

        item = _make_drive_item("video-001", "meeting.mp4")
        item["file"] = {"mimeType": "video/mp4"}
        response_data = _make_delta_response([item], delta_link="https://delta.link/token4")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        added_events = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_events) == 0

    @pytest.mark.asyncio
    async def test_content_change_classified_as_updated(self, mock_db, mock_graph):
        """When cTag changes, the item should be classified as UPDATED."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-upd",
            name="report.pdf",
            site_id="site-001",
            file_type="pdf",
            sharepoint_url="https://contoso.sharepoint.com/report.pdf",
            ctag="c:{old-version}",
            etag="e:{old-version}",
            status=ProcessingStatus.COMPLETED,
        )

        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        mock_doc_result = MagicMock()
        mock_doc_result.scalar_one_or_none.return_value = existing_doc

        mock_db.execute.side_effect = [mock_cursor_result, mock_doc_result]

        item = _make_drive_item("item-upd", "report.pdf", ctag="c:{new-version}", etag="e:{new-version}")
        response_data = _make_delta_response([item], delta_link="https://delta.link/token5")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        added_events = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_events) == 1
        assert added_events[0].change_type == ChangeType.UPDATED

    @pytest.mark.asyncio
    async def test_permission_change_classified_correctly(self, mock_db, mock_graph):
        """When only eTag changes (cTag same), classify as PERMISSION_CHANGED."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-perm",
            name="policy.docx",
            site_id="site-001",
            file_type="docx",
            sharepoint_url="https://contoso.sharepoint.com/policy.docx",
            ctag="c:{same}",
            etag="e:{old}",
            status=ProcessingStatus.COMPLETED,
        )

        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        mock_doc_result = MagicMock()
        mock_doc_result.scalar_one_or_none.return_value = existing_doc

        mock_db.execute.side_effect = [mock_cursor_result, mock_doc_result]

        item = _make_drive_item("item-perm", "policy.docx", ctag="c:{same}", etag="e:{new}")
        response_data = _make_delta_response([item], delta_link="https://delta.link/token6")

        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        added_events = [call.args[0] for call in mock_db.add.call_args_list if isinstance(call.args[0], SyncEvent)]
        assert len(added_events) == 1
        assert added_events[0].change_type == ChangeType.PERMISSION_CHANGED


class TestDeltaSyncTokenManagement:
    """Tests for delta token lifecycle management."""

    @pytest.mark.asyncio
    async def test_expired_token_resets_cursor(self, mock_db, mock_graph):
        """HTTP 410 (Gone) should reset the delta token for re-crawl."""
        cursor = SyncCursor(
            site_id="site-001",
            drive_id="drive-001",
            delta_token="https://old.delta.link",
            token_obtained_at=datetime.now(timezone.utc),
        )
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor
        mock_db.execute.return_value = mock_cursor_result

        mock_graph.get.side_effect = GraphTokenExpiredError(
            410, "Gone", "https://old.delta.link"
        )

        manager = DeltaSyncManager(mock_graph, mock_db)

        with pytest.raises(GraphTokenExpiredError):
            await manager.sync_drive("site-001", "drive-001")

        # Delta token should be reset
        assert cursor.delta_token is None

    @pytest.mark.asyncio
    async def test_old_token_triggers_full_crawl(self, mock_db, mock_graph):
        """Tokens older than max age should trigger a fresh delta crawl."""
        cursor = SyncCursor(
            site_id="site-001",
            drive_id="drive-001",
            delta_token="https://old.token",
            token_obtained_at=datetime.now(timezone.utc) - timedelta(days=26),
        )
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor
        mock_db.execute.return_value = mock_cursor_result

        response_data = _make_delta_response([], delta_link="https://new.delta.link")
        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_graph.get.return_value = mock_response

        manager = DeltaSyncManager(mock_graph, mock_db)
        await manager.sync_drive("site-001", "drive-001")

        # Should have called with the base delta URL, not the old token
        first_call_url = mock_graph.get.call_args_list[0].args[0]
        assert "/drives/drive-001/root/delta" in first_call_url

    @pytest.mark.asyncio
    async def test_pagination_follows_next_link(self, mock_db, mock_graph):
        """Delta sync should follow @odata.nextLink for multi-page results."""
        cursor = SyncCursor(site_id="site-001", drive_id="drive-001")
        mock_cursor_result = MagicMock()
        mock_cursor_result.scalar_one_or_none.return_value = cursor

        # First call returns no doc (for new item classification)
        mock_doc_result = MagicMock()
        mock_doc_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [mock_cursor_result, mock_doc_result, mock_doc_result]

        item1 = _make_drive_item("item-p1", "page1.pdf")
        item2 = _make_drive_item("item-p2", "page2.docx")

        page1 = _make_delta_response([item1], next_link="https://next.page/2")
        page2 = _make_delta_response([item2], delta_link="https://final.delta.link")

        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = page1
        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = page2

        mock_graph.get.side_effect = [mock_resp1, mock_resp2]

        manager = DeltaSyncManager(mock_graph, mock_db)
        changes = await manager.sync_drive("site-001", "drive-001")

        # Should have followed pagination
        assert mock_graph.get.call_count == 2
        # New delta token should be saved
        assert cursor.delta_token == "https://final.delta.link"


# ── ChangeHandler Tests ──

class TestChangeHandler:
    """Tests for CRUD change routing."""

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_create_dispatches_ingestion(self, mock_task, mock_db, mock_graph):
        """CREATE event should create a Document and dispatch ingestion task."""
        mock_task.apply_async.return_value = MagicMock(id="task-001")

        item = _make_drive_item("item-new", "report.pdf")
        event = SyncEvent(
            drive_id="drive-001",
            drive_item_id="item-new",
            change_type=ChangeType.CREATED,
            file_name="report.pdf",
            raw_delta_item=item,
        )

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes([event])

        assert stats["created"] == 1
        mock_task.apply_async.assert_called_once()

        call_kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
        assert call_kwargs["operation"] == "create"
        assert call_kwargs["drive_id"] == "drive-001"

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_update_dispatches_ingestion(self, mock_task, mock_db, mock_graph):
        """UPDATE event should update the Document and dispatch ingestion."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-upd",
            name="report.pdf",
            site_id="site-001",
            file_type="pdf",
            sharepoint_url="https://contoso.sharepoint.com/report.pdf",
            status=ProcessingStatus.COMPLETED,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_doc
        mock_db.execute.return_value = mock_result

        mock_task.apply_async.return_value = MagicMock(id="task-002")

        item = _make_drive_item("item-upd", "report.pdf", ctag="c:{new}")
        event = SyncEvent(
            drive_id="drive-001",
            drive_item_id="item-upd",
            change_type=ChangeType.UPDATED,
            file_name="report.pdf",
            raw_delta_item=item,
        )

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes([event])

        assert stats["updated"] == 1
        call_kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
        assert call_kwargs["operation"] == "update"

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_delete_dispatches_ingestion(self, mock_task, mock_db, mock_graph):
        """DELETE event should mark doc as DELETING and dispatch delete task."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-del",
            name="old.pdf",
            site_id="site-001",
            file_type="pdf",
            sharepoint_url="https://contoso.sharepoint.com/old.pdf",
            status=ProcessingStatus.COMPLETED,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_doc
        mock_db.execute.return_value = mock_result

        mock_task.apply_async.return_value = MagicMock(id="task-003")

        event = SyncEvent(
            drive_id="drive-001",
            drive_item_id="item-del",
            change_type=ChangeType.DELETED,
            file_name="old.pdf",
            raw_delta_item={"id": "item-del", "deleted": {"state": "deleted"}},
        )

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes([event])

        assert stats["deleted"] == 1
        assert existing_doc.status == ProcessingStatus.DELETING
        call_kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
        assert call_kwargs["operation"] == "delete"

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_permission_change_dispatches_refresh(self, mock_task, mock_db, mock_graph):
        """PERMISSION_CHANGED event should dispatch a lightweight refresh."""
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-perm",
            name="policy.docx",
            site_id="site-001",
            file_type="docx",
            sharepoint_url="https://contoso.sharepoint.com/policy.docx",
            status=ProcessingStatus.COMPLETED,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_doc
        mock_db.execute.return_value = mock_result

        mock_task.apply_async.return_value = MagicMock(id="task-004")

        item = _make_drive_item("item-perm", "policy.docx", ctag="c:{same}", etag="e:{new}")
        event = SyncEvent(
            drive_id="drive-001",
            drive_item_id="item-perm",
            change_type=ChangeType.PERMISSION_CHANGED,
            file_name="policy.docx",
            raw_delta_item=item,
        )

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes([event])

        assert stats["permission"] == 1
        call_kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
        assert call_kwargs["operation"] == "refresh_permissions"

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_update_missing_doc_becomes_create(self, mock_task, mock_db, mock_graph):
        """If UPDATE arrives for a doc not in DB, it should be treated as CREATE."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        mock_task.apply_async.return_value = MagicMock(id="task-005")

        item = _make_drive_item("item-mystery", "mystery.pdf")
        event = SyncEvent(
            drive_id="drive-001",
            drive_item_id="item-mystery",
            change_type=ChangeType.UPDATED,
            file_name="mystery.pdf",
            raw_delta_item=item,
        )

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes([event])

        # Should be counted as created (fallback)
        assert stats["created"] == 1

    @pytest.mark.asyncio
    @patch("src.connectors.change_handlers.run_ingestion_task")
    async def test_mixed_batch_processing(self, mock_task, mock_db, mock_graph):
        """A batch with mixed change types should all be processed correctly."""
        mock_task.apply_async.return_value = MagicMock(id="task-batch")

        # DELETE needs existing doc
        existing_doc = Document(
            id=uuid.uuid4(),
            drive_id="drive-001",
            drive_item_id="item-del",
            name="delete-me.pdf",
            site_id="site-001",
            file_type="pdf",
            sharepoint_url="https://test.com",
            status=ProcessingStatus.COMPLETED,
        )

        mock_result_none = MagicMock()
        mock_result_none.scalar_one_or_none.return_value = None
        mock_result_found = MagicMock()
        mock_result_found.scalar_one_or_none.return_value = existing_doc

        # First call for create (returns None), second for delete (returns doc)
        mock_db.execute.side_effect = [mock_result_none, mock_result_found]

        events = [
            SyncEvent(
                drive_id="drive-001",
                drive_item_id="item-new",
                change_type=ChangeType.CREATED,
                file_name="new.pdf",
                raw_delta_item=_make_drive_item("item-new", "new.pdf"),
            ),
            SyncEvent(
                drive_id="drive-001",
                drive_item_id="item-del",
                change_type=ChangeType.DELETED,
                file_name="delete-me.pdf",
                raw_delta_item={"id": "item-del", "deleted": {"state": "deleted"}},
            ),
        ]

        handler = ChangeHandler(mock_db, mock_graph)
        stats = await handler.process_changes(events)

        assert stats["created"] == 1
        assert stats["deleted"] == 1
        assert mock_task.apply_async.call_count == 2
