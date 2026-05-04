"""Celery application configuration and task definitions."""

from __future__ import annotations

import json
import structlog
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from config.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()

# ── Celery Application ──

celery_app = Celery(
    "better-rag",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
)

celery_app.conf.update(
    # Task routing
    task_routes={
        "src.celery_app.run_query_task": {"queue": "rag.query"},
        "src.celery_app.run_docgen_task": {"queue": "rag.doc_gen"},
        "src.celery_app.run_ingestion_task": {"queue": "rag.ingestion"},
        "src.celery_app.run_delta_sync": {"queue": "rag.ingestion"},
        "src.celery_app.run_delta_sync_all": {"queue": "rag.ingestion"},
        "src.celery_app.run_full_reconciliation": {"queue": "rag.ingestion"},
        "src.celery_app.run_permission_refresh": {"queue": "rag.ingestion"},
        "src.celery_app.retry_failed_documents": {"queue": "rag.ingestion"},
        "src.celery_app.renew_webhooks": {"queue": "rag.ingestion"},
        "src.celery_app.cleanup_streams": {"queue": "rag.query"},
        "src.celery_app.cleanup_checkpoints": {"queue": "rag.query"},
        # Customer Care KB
        "src.celery_app.run_cc_ingestion_task": {"queue": "rag.ingestion"},
        "src.celery_app.run_cc_delta_sync": {"queue": "rag.ingestion"},
        "src.celery_app.run_cc_delta_sync_all": {"queue": "rag.ingestion"},
    },
    # Worker prefetch
    worker_prefetch_multiplier=1,
    # Acknowledge after completion
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Timeouts
    task_soft_time_limit=120,
    task_time_limit=180,
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Disable result storage for streaming tasks
    task_ignore_result=True,
    # Beat schedule — empty when BEAT_ENABLED=False (manual trigger only)
    beat_schedule={
        "delta-sync-all-drives": {
            "task": "src.celery_app.run_delta_sync_all",
            "schedule": crontab(minute=f"*/{settings.DELTA_SYNC_INTERVAL_MINUTES}"),
            "options": {"queue": "rag.ingestion"},
        },
        "daily-full-reconciliation": {
            "task": "src.celery_app.run_full_reconciliation",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "rag.ingestion"},
        },
        "renew-webhook-subscriptions": {
            "task": "src.celery_app.renew_webhooks",
            "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
            "options": {"queue": "rag.ingestion"},
        },
        "refresh-permissions": {
            "task": "src.celery_app.run_permission_refresh",
            "schedule": crontab(minute=0, hour="*/4"),
            "options": {"queue": "rag.ingestion"},
        },
        "retry-failed-documents": {
            "task": "src.celery_app.retry_failed_documents",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "rag.ingestion"},
        },
        "cleanup-expired-streams": {
            "task": "src.celery_app.cleanup_streams",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "rag.query"},
        },
        "cleanup-checkpoints": {
            "task": "src.celery_app.cleanup_checkpoints",
            "schedule": crontab(minute=0, hour="*/1"),
            "options": {"queue": "rag.query"},
        },
        # Customer Care KB — same interval as main KB delta sync
        "cc-delta-sync-all-drives": {
            "task": "src.celery_app.run_cc_delta_sync_all",
            "schedule": crontab(minute=f"*/{settings.DELTA_SYNC_INTERVAL_MINUTES}"),
            "options": {"queue": "rag.ingestion"},
        },
    } if settings.BEAT_ENABLED else {},
)


# ── Task Definitions ──

@celery_app.task(bind=True, queue="rag.query")
def run_query_task(self, messages, user_id, user_email, stream_key):
    """Execute LangGraph orchestrator and publish events to Redis Stream."""
    import asyncio
    asyncio.run(_run_query_async(messages, user_id, user_email, stream_key))


async def _run_query_async(messages, user_id, user_email, stream_key):
    """Async wrapper for LangGraph execution with Redis Stream publishing."""
    import redis.asyncio as aioredis
    from src.graph.orchestrator import run_orchestrator

    stream_client = aioredis.from_url(settings.redis_streams_url)

    try:
        async for event in run_orchestrator(
            messages=messages,
            user_id=user_id,
            user_email=user_email,
        ):
            await stream_client.xadd(
                stream_key,
                {"data": json.dumps(event)},
                maxlen=1000,
            )

        await stream_client.xadd(stream_key, {"data": "[DONE]"})
        await stream_client.expire(stream_key, 300)

    except Exception as e:
        await stream_client.xadd(
            stream_key,
            {"data": json.dumps({"type": "error", "message": str(e)})},
        )
        await stream_client.xadd(stream_key, {"data": "[DONE]"})
    finally:
        await stream_client.aclose()


@celery_app.task(
    bind=True,
    queue="rag.ingestion",
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=600,
    time_limit=900,
)
def run_ingestion_task(self, document_id, drive_id, drive_item_id, operation):
    """
    Unified ingestion task — routes to Create/Update/Delete/RefreshPermissions.

    Operations: "create", "update", "delete", "refresh_permissions"
    """
    import asyncio
    try:
        asyncio.run(
            _run_ingestion(document_id, drive_id, drive_item_id, operation)
        )
    except Exception as exc:
        logger.error(
            "ingestion_task.failed",
            doc_id=document_id,
            operation=operation,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _run_ingestion(document_id, drive_id, drive_item_id, operation):
    """Async ingestion handler — runs the full pipeline."""
    from src.storage.db import close_db
    from src.processing.ingestion import IngestionPipeline

    # Dispose stale engine from previous event loop (Celery prefork reuses processes)
    await close_db()

    pipeline = IngestionPipeline()
    await pipeline.run(document_id, drive_id, drive_item_id, operation)


@celery_app.task(
    bind=True,
    queue="rag.doc_gen",
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def run_docgen_task(self, doc_spec, user_id, stream_key):
    """Run document generation in a dedicated worker pool."""
    import asyncio
    asyncio.run(_run_docgen_async(doc_spec, user_id, stream_key))


async def _run_docgen_async(doc_spec, user_id, stream_key):
    """Async doc generation handler — placeholder, implemented in Phase 8."""
    logger.info("docgen.dispatched", doc_type=doc_spec.get("doc_type"))


@celery_app.task(queue="rag.ingestion")
def run_delta_sync(site_id: str, drive_id: str):
    """Delta sync a single drive (triggered by webhook or Beat)."""
    import asyncio
    asyncio.run(_delta_sync_drive(site_id, drive_id))


async def _delta_sync_drive(site_id: str, drive_id: str):
    """Async delta sync for a single drive."""
    from src.connectors.change_handlers import ChangeHandler
    from src.connectors.delta_sync import DeltaSyncManager
    from src.connectors.graph_client import GraphClientFactory
    from src.storage.db import get_db_session

    from config.settings import get_settings
    from src.models.sync import SyncCursor

    settings = get_settings()
    max_files = settings.INGESTION_MAX_FILES
    graph_client = await GraphClientFactory.create()
    try:
        async with get_db_session() as db:
            manager = DeltaSyncManager(graph_client, db)
            changes = await manager.sync_drive(site_id, drive_id)
            if changes:
                handler = ChangeHandler(db, graph_client)
                stats = await handler.process_changes(
                    changes,
                    max_files=max_files,
                    path_filter=settings.INGESTION_PATH_FILTER,
                    path_exclude=settings.INGESTION_PATH_EXCLUDE,
                )
                # If files were skipped, reset delta token so next sync replays them
                if stats.get("skipped", 0) > 0:
                    result = await db.execute(
                        select(SyncCursor).where(SyncCursor.drive_id == drive_id)
                    )
                    cursor = result.scalar_one_or_none()
                    if cursor:
                        cursor.delta_token = None
                        logger.info(
                            "delta_sync.token_reset_for_batch",
                            drive_id=drive_id,
                            skipped=stats["skipped"],
                            ingested=stats["created"] + stats["updated"],
                        )
    finally:
        await graph_client.close()


@celery_app.task(queue="rag.ingestion")
def run_delta_sync_all():
    """Delta sync all configured drives (Beat fallback)."""
    import asyncio
    asyncio.run(_delta_sync_all_drives())


async def _delta_sync_all_drives():
    """Delta sync all configured drives."""
    drives = settings.get_sharepoint_drives()
    for drive_config in drives:
        site_id = drive_config.get("site_id", "")
        drive_id = drive_config.get("drive_id", "")
        if site_id and drive_id:
            run_delta_sync.apply_async(
                kwargs={"site_id": site_id, "drive_id": drive_id},
                queue="rag.ingestion",
                task_id=f"delta_sync_{drive_id}",
            )


@celery_app.task(queue="rag.ingestion")
def run_full_reconciliation():
    """Daily full reconciliation — reset delta tokens and re-crawl all drives."""
    import asyncio
    asyncio.run(_full_reconciliation())


async def _full_reconciliation():
    """
    Daily full reconciliation:

    For each configured drive:
    1. Enumerate all current items in SharePoint (via children API)
    2. Compare against our documents table
    3. Detect orphans (in our DB but deleted from SharePoint) → mark deleted
    4. Run a full delta sync to pick up any new/changed items

    This catches edge cases that delta queries miss:
    - Expired delta tokens
    - Webhook notification drops
    - Items deleted while our system was down
    """
    from sqlalchemy import select, update

    from src.connectors.change_handlers import ChangeHandler
    from src.connectors.delta_sync import DeltaSyncManager
    from src.connectors.graph_client import GraphClientFactory
    from src.models.document import Document
    from src.models.enums import ProcessingStatus
    from src.models.sync import SyncCursor
    from src.storage.db import get_db_session

    drives = settings.get_sharepoint_drives()
    if not drives:
        logger.info("reconciliation.no_drives_configured")
        return

    graph_client = await GraphClientFactory.create()
    try:
        for drive_config in drives:
            site_id = drive_config.get("site_id", "")
            drive_id = drive_config.get("drive_id", "")
            if not site_id or not drive_id:
                continue

            logger.info("reconciliation.drive_start", drive_id=drive_id)

            try:
                async with get_db_session() as db:
                    manager = DeltaSyncManager(graph_client, db)

                    # Step 1: Get all item IDs currently in SharePoint
                    sp_item_ids = await manager.get_all_drive_item_ids(drive_id)

                    # Step 2: Get all our non-deleted document item IDs for this drive
                    result = await db.execute(
                        select(Document.id, Document.drive_item_id, Document.name).where(
                            Document.drive_id == drive_id,
                            Document.status.notin_([
                                ProcessingStatus.DELETED,
                                ProcessingStatus.DELETING,
                            ]),
                        )
                    )
                    our_docs = result.all()

                    # Step 3: Find orphans — in our DB but not in SharePoint
                    orphan_count = 0
                    for doc_id, item_id, doc_name in our_docs:
                        if item_id not in sp_item_ids:
                            logger.info(
                                "reconciliation.orphan_detected",
                                doc_id=str(doc_id),
                                item_id=item_id,
                                name=doc_name,
                            )
                            run_ingestion_task.apply_async(
                                kwargs={
                                    "document_id": str(doc_id),
                                    "drive_id": drive_id,
                                    "drive_item_id": item_id,
                                    "operation": "delete",
                                },
                                queue="rag.ingestion",
                            )
                            orphan_count += 1

                    # Step 4: Reset delta token to force full change enumeration
                    await db.execute(
                        update(SyncCursor)
                        .where(
                            SyncCursor.site_id == site_id,
                            SyncCursor.drive_id == drive_id,
                        )
                        .values(delta_token=None)
                    )

                    logger.info(
                        "reconciliation.drive_complete",
                        drive_id=drive_id,
                        sharepoint_items=len(sp_item_ids),
                        our_docs=len(our_docs),
                        orphans=orphan_count,
                    )

                # Step 5: Run delta sync to pick up new/changed items
                await _delta_sync_drive(site_id, drive_id)

            except Exception as e:
                logger.error(
                    "reconciliation.drive_failed",
                    drive_id=drive_id,
                    error=str(e),
                )

    finally:
        await graph_client.close()

    logger.info("reconciliation.completed")


@celery_app.task(queue="rag.ingestion")
def run_permission_refresh():
    """Re-expand group → user permissions for all completed documents."""
    import asyncio
    asyncio.run(_permission_refresh())


async def _permission_refresh():
    from sqlalchemy import select
    from src.connectors.graph_client import GraphClientFactory
    from src.connectors.permissions import PermissionResolver
    from src.models.document import Document
    from src.models.enums import ProcessingStatus
    from src.storage.db import get_db_session

    graph_client = await GraphClientFactory.create()
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(Document).where(Document.status == ProcessingStatus.COMPLETED)
            )
            documents = result.scalars().all()
            resolver = PermissionResolver(graph_client, db)
            await resolver.refresh_all_permissions(list(documents))
    finally:
        await graph_client.close()


@celery_app.task(queue="rag.ingestion")
def retry_failed_documents():
    """Re-queue failed documents with retry count < max_retries."""
    import asyncio
    asyncio.run(_retry_failed())


async def _retry_failed():
    from sqlalchemy import select
    from src.models.document import Document
    from src.models.enums import ProcessingStatus
    from src.storage.db import get_db_session

    async with get_db_session() as db:
        result = await db.execute(
            select(Document).where(
                Document.status == ProcessingStatus.FAILED,
                Document.retry_count < settings.INGESTION_MAX_RETRIES,
            )
        )
        docs = result.scalars().all()
        for doc in docs:
            doc.status = ProcessingStatus.PENDING
            run_ingestion_task.apply_async(
                kwargs={
                    "document_id": str(doc.id),
                    "drive_id": doc.drive_id,
                    "drive_item_id": doc.drive_item_id,
                    "operation": "create",
                },
                queue="rag.ingestion",
            )
        logger.info("retry_failed.dispatched", count=len(docs))


@celery_app.task(queue="rag.ingestion")
def renew_webhooks():
    """Renew Graph webhook subscriptions for all monitored drives."""
    import asyncio
    asyncio.run(_renew_webhooks())


async def _renew_webhooks():
    from src.connectors.graph_client import GraphClientFactory
    from src.connectors.webhooks import WebhookManager
    from src.storage.db import get_db_session

    graph_client = await GraphClientFactory.create()
    try:
        async with get_db_session() as db:
            manager = WebhookManager(graph_client, db)
            drives = settings.get_sharepoint_drives()
            for drive in drives:
                await manager.ensure_subscription(
                    site_id=drive.get("site_id", ""),
                    drive_id=drive.get("drive_id", ""),
                )
    except Exception as e:
        logger.error("renew_webhooks.failed", error=str(e))
    finally:
        await graph_client.close()


@celery_app.task(queue="rag.query")
def cleanup_streams():
    """Clean up expired Redis Streams."""
    import asyncio
    asyncio.run(_cleanup_streams())


async def _cleanup_streams():
    import redis.asyncio as aioredis
    client = aioredis.from_url(settings.redis_streams_url)
    try:
        # Find and delete stale streams (older than 10 minutes)
        cursor = b"0"
        while True:
            cursor, keys = await client.scan(cursor, match="rag:stream:*", count=100)
            for key in keys:
                stream_len = await client.xlen(key)
                if stream_len == 0:
                    await client.delete(key)
            if cursor == b"0":
                break
    finally:
        await client.aclose()


@celery_app.task(queue="rag.query")
def cleanup_checkpoints():
    """Clean up expired LangGraph checkpoints — placeholder."""
    logger.info("cleanup_checkpoints.started")


# ── Customer Care KB Tasks ──────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    queue="rag.ingestion",
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=600,
    time_limit=900,
)
def run_cc_ingestion_task(self, document_id, drive_id, drive_item_id, operation):
    """
    Unified CC ingestion task — routes to Create/Update/Delete/RefreshPermissions
    for Customer Care knowledge base documents.

    Operations: "create", "update", "delete", "refresh_permissions"
    """
    import asyncio
    try:
        asyncio.run(
            _run_cc_ingestion(document_id, drive_id, drive_item_id, operation)
        )
    except Exception as exc:
        logger.error(
            "cc_ingestion_task.failed",
            doc_id=document_id,
            operation=operation,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


async def _run_cc_ingestion(document_id, drive_id, drive_item_id, operation):
    """Async CC ingestion handler."""
    from src.customer_care.ingestion import CCIngestionPipeline

    pipeline = CCIngestionPipeline()
    await pipeline.run(document_id, drive_id, drive_item_id, operation)


@celery_app.task(queue="rag.ingestion")
def run_cc_delta_sync(site_id: str, drive_id: str):
    """Delta sync a single CC drive (triggered by beat or manual call)."""
    import asyncio
    asyncio.run(_cc_delta_sync_drive(site_id, drive_id))


async def _cc_delta_sync_drive(site_id: str, drive_id: str):
    """Async delta sync for a single CC drive."""
    from src.connectors.change_handlers import ChangeHandler
    from src.connectors.graph_client import GraphClientFactory
    from src.customer_care.change_handler import CCChangeHandler
    from src.customer_care.delta_sync import CCDeltaSyncManager
    from src.storage.db import get_db_session

    graph_client = await GraphClientFactory.create()
    try:
        async with get_db_session() as db:
            manager = CCDeltaSyncManager(graph_client, db)
            changes = await manager.sync_drive(site_id, drive_id)
            if changes:
                handler = CCChangeHandler(db, graph_client)
                await handler.process_changes(changes)
    finally:
        await graph_client.close()


@celery_app.task(queue="rag.ingestion")
def run_cc_delta_sync_all():
    """Delta sync all configured CC drives (beat schedule)."""
    import asyncio
    asyncio.run(_cc_delta_sync_all_drives())


async def _cc_delta_sync_all_drives():
    """Delta sync all Customer Care SharePoint drives."""
    drives = settings.get_cc_sharepoint_drives()
    for drive_config in drives:
        site_id = drive_config.get("site_id", "")
        drive_id = drive_config.get("drive_id", "")
        if site_id and drive_id:
            run_cc_delta_sync.apply_async(
                kwargs={"site_id": site_id, "drive_id": drive_id},
                queue="rag.ingestion",
                task_id=f"cc_delta_sync_{drive_id}",
            )
