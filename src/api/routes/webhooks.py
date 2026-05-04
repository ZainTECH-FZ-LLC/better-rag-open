"""Microsoft Graph webhook notification endpoint."""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Request, Response

from config.settings import get_settings

logger = structlog.get_logger()
router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/graph/notify")
async def graph_webhook(request: Request):
    """
    Microsoft Graph webhook notification endpoint.

    Two modes:
    1. Validation — Graph sends a validationToken query param on subscription
       creation.  We echo it back as plain text within 10 seconds.
    2. Notification — Graph POSTs a JSON body listing changed resources.
       We ACK immediately (202) and dispatch a Celery delta sync task.
       The webhook payload tells us WHICH drive changed, not WHAT changed.
       Delta query discovers the actual file-level changes.

    Dedup: Celery task_id = f"delta_sync_{drive_id}" ensures multiple
    notifications for the same drive within seconds collapse into a single task.
    """
    settings = get_settings()

    # ── Subscription Validation ──
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        logger.info("webhook.validation", token_prefix=validation_token[:20])
        return Response(content=validation_token, media_type="text/plain")

    # ── Change Notification ──
    body = await request.json()
    notifications = body.get("value", [])

    if not notifications:
        return Response(status_code=202)

    from src.celery_app import run_delta_sync

    dispatched = 0
    for notification in notifications:
        resource = notification.get("resource", "")
        client_state = notification.get("clientState", "")

        # Verify clientState to protect against spoofed notifications.
        # clientState format: "{site_id}:{drive_id}" or
        # "{site_id}:{drive_id}:{webhook_secret}"
        if not _verify_client_state(client_state, settings):
            logger.warn(
                "webhook.invalid_client_state",
                resource=resource,
            )
            continue

        # Extract drive_id from resource path (e.g. "drives/{drive-id}/root")
        drive_id = _extract_drive_id(resource)
        if not drive_id:
            logger.warn("webhook.unparseable_resource", resource=resource)
            continue

        # Extract site_id from clientState
        site_id = client_state.split(":")[0] if ":" in client_state else ""

        logger.info(
            "webhook.notification",
            drive_id=drive_id,
            site_id=site_id,
            change_type=notification.get("changeType"),
            subscription_id=notification.get("subscriptionId"),
        )

        # Dispatch delta sync — task_id deduplicates rapid-fire notifications
        run_delta_sync.apply_async(
            kwargs={"site_id": site_id, "drive_id": drive_id},
            queue="rag.ingestion",
            task_id=f"delta_sync_{drive_id}",
        )
        dispatched += 1

    logger.info(
        "webhook.batch_processed",
        total=len(notifications),
        dispatched=dispatched,
    )

    # Graph requires 202 within 10 seconds
    return Response(status_code=202)


def _extract_drive_id(resource: str) -> str | None:
    """Extract drive_id from a Graph resource path like 'drives/{id}/root'."""
    parts = resource.split("/")
    if len(parts) >= 2 and parts[0] == "drives":
        return parts[1]
    return None


def _verify_client_state(client_state: str, settings) -> bool:
    """
    Verify the clientState token matches what we set on subscription creation.

    If WEBHOOK_CLIENT_STATE is configured, the clientState must end with it.
    If not configured, all notifications are accepted (development mode).
    """
    if not settings.WEBHOOK_CLIENT_STATE:
        return True

    parts = client_state.split(":")
    if len(parts) >= 3:
        received_secret = parts[2]
        return hmac.compare_digest(received_secret, settings.WEBHOOK_CLIENT_STATE)

    return False
