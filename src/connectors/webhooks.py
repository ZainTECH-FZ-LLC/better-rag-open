"""Microsoft Graph webhook subscription management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.connectors.graph_client import GraphClient
from src.models.sync import SyncCursor

logger = structlog.get_logger()


class WebhookManager:
    """Create and renew Microsoft Graph change notification subscriptions."""

    def __init__(self, graph: GraphClient, db: AsyncSession):
        self.graph = graph
        self.db = db
        self.settings = get_settings()

    async def ensure_subscription(self, site_id: str, drive_id: str) -> None:
        """Create or renew a webhook subscription for a drive."""
        cursor = await self._get_cursor(site_id, drive_id)

        if cursor and cursor.webhook_subscription_id and cursor.webhook_expiry:
            # Check if still valid (renew if expiring within 2 days)
            if cursor.webhook_expiry > datetime.now(timezone.utc) + timedelta(days=2):
                logger.debug("webhook.still_valid", drive_id=drive_id)
                return

            # Renew
            try:
                await self._renew_subscription(cursor)
                return
            except Exception as e:
                logger.warn("webhook.renew_failed", drive_id=drive_id, error=str(e))
                # Fall through to create new subscription

        # Create new subscription
        await self._create_subscription(site_id, drive_id, cursor)

    async def _create_subscription(
        self, site_id: str, drive_id: str, cursor: SyncCursor | None
    ) -> None:
        """Create a new Graph webhook subscription."""
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
        notification_url = f"{self.settings.PUBLIC_BASE_URL}/webhooks/graph/notify"
        client_state = f"{site_id}:{drive_id}"

        if self.settings.WEBHOOK_CLIENT_STATE:
            client_state += f":{self.settings.WEBHOOK_CLIENT_STATE}"

        payload = {
            "changeType": "updated",
            "notificationUrl": notification_url,
            "resource": f"drives/{drive_id}/root",
            "expirationDateTime": expiry.isoformat() + "Z",
            "clientState": client_state,
        }

        response = await self.graph.post("/subscriptions", json=payload)
        data = response.json()
        subscription_id = data.get("id", "")

        if cursor is None:
            cursor = SyncCursor(site_id=site_id, drive_id=drive_id)
            self.db.add(cursor)

        cursor.webhook_subscription_id = subscription_id
        cursor.webhook_expiry = expiry
        await self.db.flush()

        logger.info(
            "webhook.created",
            drive_id=drive_id,
            subscription_id=subscription_id,
            expires=expiry.isoformat(),
        )

    async def _renew_subscription(self, cursor: SyncCursor) -> None:
        """Renew an existing subscription."""
        expiry = datetime.now(timezone.utc) + timedelta(days=30)

        await self.graph.patch(
            f"/subscriptions/{cursor.webhook_subscription_id}",
            json={"expirationDateTime": expiry.isoformat() + "Z"},
        )

        cursor.webhook_expiry = expiry
        await self.db.flush()

        logger.info(
            "webhook.renewed",
            subscription_id=cursor.webhook_subscription_id,
            expires=expiry.isoformat(),
        )

    async def _get_cursor(
        self, site_id: str, drive_id: str
    ) -> SyncCursor | None:
        result = await self.db.execute(
            select(SyncCursor).where(
                SyncCursor.site_id == site_id,
                SyncCursor.drive_id == drive_id,
            )
        )
        return result.scalar_one_or_none()
