"""CC delta sync manager — subclasses DeltaSyncManager to route lookups to cc_documents."""

from __future__ import annotations

from sqlalchemy import select

from src.connectors.delta_sync import DeltaSyncManager
from src.customer_care.models import CCDocument
from src.models.enums import ProcessingStatus


class CCDeltaSyncManager(DeltaSyncManager):
    """
    Delta sync manager for the Customer Care knowledge base.

    Inherits all delta-sync logic (pagination, token management, change classification)
    from DeltaSyncManager unchanged.

    The only override is _find_document, which queries CCDocument (cc_documents table)
    instead of Document (documents table) when classifying Create vs Update vs
    Permission_Changed changes.
    """

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
