"""Unit tests for delta sync classification logic."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.connectors.delta_sync import DeltaSyncManager, _get_extension
from src.models.enums import ChangeType


class TestGetExtension:
    def test_pdf(self):
        assert _get_extension("report.pdf") == ".pdf"

    def test_docx(self):
        assert _get_extension("memo.docx") == ".docx"

    def test_no_extension(self):
        assert _get_extension("README") == ""

    def test_double_extension(self):
        assert _get_extension("file.backup.xlsx") == ".xlsx"

    def test_uppercase(self):
        assert _get_extension("FILE.PDF") == ".pdf"


class TestChangeClassification:
    """Test that delta items are classified correctly."""

    @pytest.fixture
    def manager(self):
        graph = MagicMock()
        db = MagicMock()
        db.add = MagicMock()
        db.execute = AsyncMock()
        return DeltaSyncManager(graph, db)

    @pytest.mark.asyncio
    async def test_deleted_item(self, manager):
        """Items with 'deleted' facet should be classified as DELETE."""
        manager._find_document = AsyncMock(return_value=MagicMock(name="old.pdf"))

        item = {"id": "item-1", "deleted": {"state": "deleted"}}
        event = await manager._classify_and_record("drive-1", item)

        assert event is not None
        assert event.change_type == ChangeType.DELETED

    @pytest.mark.asyncio
    async def test_folder_skipped(self, manager):
        """Folders should be skipped."""
        item = {"id": "folder-1", "name": "Folder", "folder": {}}
        event = await manager._classify_and_record("drive-1", item)
        assert event is None

    @pytest.mark.asyncio
    async def test_unsupported_extension(self, manager):
        """Files with unsupported extensions should be skipped."""
        item = {"id": "item-1", "name": "image.png", "file": {"mimeType": "image/png"}}
        event = await manager._classify_and_record("drive-1", item)
        assert event is None

    @pytest.mark.asyncio
    async def test_new_file_created(self, manager):
        """New file not in DB should be classified as CREATED."""
        manager._find_document = AsyncMock(return_value=None)

        item = {
            "id": "item-new",
            "name": "report.pdf",
            "file": {"mimeType": "application/pdf"},
            "cTag": "c:{v1}",
            "eTag": "e:{v1}",
        }
        event = await manager._classify_and_record("drive-1", item)

        assert event is not None
        assert event.change_type == ChangeType.CREATED

    @pytest.mark.asyncio
    async def test_content_updated(self, manager):
        """Changed cTag means content update."""
        existing = MagicMock()
        existing.ctag = "c:{old}"
        existing.etag = "e:{old}"
        manager._find_document = AsyncMock(return_value=existing)

        item = {
            "id": "item-1",
            "name": "report.pdf",
            "file": {"mimeType": "application/pdf"},
            "cTag": "c:{new}",
            "eTag": "e:{new}",
        }
        event = await manager._classify_and_record("drive-1", item)

        assert event is not None
        assert event.change_type == ChangeType.UPDATED

    @pytest.mark.asyncio
    async def test_permission_changed(self, manager):
        """Same cTag but different eTag means permission change."""
        existing = MagicMock()
        existing.ctag = "c:{same}"
        existing.etag = "e:{old}"
        manager._find_document = AsyncMock(return_value=existing)

        item = {
            "id": "item-1",
            "name": "report.pdf",
            "file": {"mimeType": "application/pdf"},
            "cTag": "c:{same}",
            "eTag": "e:{new}",
        }
        event = await manager._classify_and_record("drive-1", item)

        assert event is not None
        assert event.change_type == ChangeType.PERMISSION_CHANGED

    @pytest.mark.asyncio
    async def test_no_change(self, manager):
        """Same cTag and eTag means no change."""
        existing = MagicMock()
        existing.ctag = "c:{same}"
        existing.etag = "e:{same}"
        manager._find_document = AsyncMock(return_value=existing)

        item = {
            "id": "item-1",
            "name": "report.pdf",
            "file": {"mimeType": "application/pdf"},
            "cTag": "c:{same}",
            "eTag": "e:{same}",
        }
        event = await manager._classify_and_record("drive-1", item)
        assert event is None
