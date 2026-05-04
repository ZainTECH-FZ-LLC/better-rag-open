"""Full ingestion pipeline — download → OCR → chunk → embed → graph → RBAC."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from config.settings import get_settings
from src.chunking.adaptive_chunker import AdaptiveSemanticChunker
from src.connectors.graph_client import GraphClient, GraphClientFactory
from src.connectors.permissions import PermissionResolver
from src.embedding.azure_openai import AzureOpenAIEmbedder
from src.knowledge_graph.builder import GraphBuilder
from src.models.document import Document
from src.models.enums import ProcessingStatus
from src.processing.pipeline import DocumentProcessingPipeline
from src.storage.blob_store import AzureBlobStore
from src.storage.db import get_db_session
from src.storage.vector_store import PgVectorStore

logger = structlog.get_logger()


class IngestionPipeline:
    """
    Full ingestion pipeline for a single document.

    Uses 4-phase DB session pattern to avoid connection timeouts during
    long-running vision/LLM processing (Azure PostgreSQL kills idle
    connections after ~5 min).

    Operations:
    - create: download → stage → OCR → chunk → embed → graph → RBAC
    - update: delete old artifacts → re-run create pipeline
    - delete: remove all artifacts (chunks, embeddings, graph, blob)
    - refresh_permissions: re-expand RBAC only (no content re-processing)
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(
        self,
        document_id: str,
        drive_id: str,
        drive_item_id: str,
        operation: str,
    ) -> None:
        """Execute the appropriate pipeline for the given operation."""
        graph_client = await GraphClientFactory.create()

        try:
            if operation == "create":
                await self._run_create(document_id, graph_client)
            elif operation == "update":
                await self._run_update(document_id, graph_client)
            elif operation == "delete":
                await self._run_delete(document_id)
            elif operation == "refresh_permissions":
                await self._run_refresh_permissions(document_id, graph_client)
            else:
                logger.error("ingestion.unknown_operation", operation=operation)
        finally:
            await graph_client.close()

    async def _run_create(
        self, document_id: str, graph_client: GraphClient
    ) -> None:
        """Full ingestion using 4-phase DB session pattern."""

        # ── Phase 1: Quick DB read + download ──
        async with get_db_session() as db:
            doc = await self._get_document(db, document_id)
            if doc is None:
                logger.error("ingestion.document_not_found", doc_id=document_id)
                return

            doc.status = ProcessingStatus.DOWNLOADING
            doc.processing_started_at = datetime.now(timezone.utc)
            await db.flush()

            # Capture fields needed outside session
            doc_id = doc.id
            doc_name = doc.name
            doc_file_type = doc.file_type
            doc_drive_id = doc.drive_id
            doc_drive_item_id = doc.drive_item_id
            doc_content_hash = doc.content_hash
            doc_created_by = doc.created_by
            doc_modified_by = doc.modified_by
            doc_sharepoint_url = doc.sharepoint_url
            doc_parent_path = doc.parent_path
            doc_created_at = doc.created_at

        try:
            # Download file
            download_url = await graph_client.get_download_url(doc_drive_id, doc_drive_item_id)
            file_bytes = await graph_client.download_file(download_url)

            # Content hash for dedup
            content_hash = hashlib.sha256(file_bytes).hexdigest()
            if doc_content_hash == content_hash:
                logger.info("ingestion.content_unchanged", doc_id=str(doc_id))
                async with get_db_session() as db:
                    doc = await self._get_document(db, str(doc_id))
                    doc.status = ProcessingStatus.COMPLETED
                return

            # Stage to Blob Storage
            blob_store = AzureBlobStore()
            blob_path = f"staging/{doc_drive_id}/{doc_drive_item_id}/{doc_name}"
            try:
                await blob_store.upload(blob_path, file_bytes)
            except Exception as e:
                blob_path = None
                logger.warn("ingestion.blob_upload_failed", error=str(e))
            finally:
                await blob_store.close()

            # Update status to processing
            async with get_db_session() as db:
                doc = await self._get_document(db, str(doc_id))
                doc.content_hash = content_hash
                if blob_path:
                    doc.blob_path = blob_path
                doc.status = ProcessingStatus.PROCESSING

            # ── Phase 2: Heavy processing — NO DB session open ──
            pipeline = DocumentProcessingPipeline()
            graph_meta = {
                "created_by": doc_created_by,
                "modified_by": doc_modified_by,
                "sharepoint_url": doc_sharepoint_url,
                "parent_path": doc_parent_path,
            }
            processed = await pipeline.process(
                file_bytes=file_bytes,
                filename=doc_name,
                file_type=doc_file_type,
                graph_metadata=graph_meta,
            )

            chunker = AdaptiveSemanticChunker()
            chunks = chunker.chunk(
                text=processed.text,
                file_type=doc_file_type,
                metadata={
                    "summary_prefix": processed.summary[:300] if processed.summary else "",
                    "section_summaries": processed.section_summaries,
                },
                parsed_doc=processed.parsed,
            )

            embedder = AzureOpenAIEmbedder()
            embeddings = await embedder.embed_texts([c.content_with_context for c in chunks])

            # ── Phase 3: Fast DB write — fresh session ──
            async with get_db_session() as db:
                doc = await self._get_document(db, str(doc_id))

                doc.summary = processed.summary
                doc.department = processed.department
                doc.content_type_tag = processed.content_type
                doc.topics = {"topics": [t["name"] for t in processed.topics]}
                doc.language = processed.language

                vector_store = PgVectorStore(db)
                chunk_dicts = [
                    {
                        "content": c.content,
                        "content_with_context": c.content_with_context,
                        "chunk_type": c.chunk_type,
                        "sequence_number": c.sequence_number,
                        "page_numbers": c.page_numbers,
                        "section_heading": c.section_heading,
                        "token_count": c.token_count,
                    }
                    for c in chunks
                ]
                chunk_ids = await vector_store.upsert_chunks(doc, chunk_dicts, embeddings)

                # RBAC permissions
                permission_resolver = PermissionResolver(graph_client, db)
                await permission_resolver.resolve_and_store(doc)

                doc.status = ProcessingStatus.COMPLETED
                doc.processing_completed_at = datetime.now(timezone.utc)
                doc.error_message = None

            # ── Phase 4: Neo4j — best-effort, outside DB session ──
            try:
                graph_builder = GraphBuilder()
                graph_chunks = [
                    {
                        "chunk_id": str(cid),
                        "chunk_index": i,
                        "summary": chunks[i].content[:200],
                    }
                    for i, cid in enumerate(chunk_ids)
                ]
                await graph_builder.index_document(
                    doc_id=str(doc_id),
                    title=doc_name,
                    department=processed.department,
                    content_type=processed.content_type,
                    access_level=None,
                    summary=processed.summary,
                    sharepoint_url=doc_sharepoint_url,
                    file_type=doc_file_type,
                    created_at=doc_created_at.isoformat() if doc_created_at else "",
                    chunks=graph_chunks,
                    entities=processed.entities,
                    topics=processed.topics,
                )
            except Exception as e:
                logger.warn("ingestion.neo4j_skipped", doc_id=str(doc_id), error=str(e))

            logger.info(
                "ingestion.completed",
                doc_id=str(doc_id),
                name=doc_name,
                chunks=len(chunks),
            )

        except Exception as e:
            # Mark as failed in a fresh session
            async with get_db_session() as db:
                doc = await self._get_document(db, str(doc_id))
                if doc:
                    doc.status = ProcessingStatus.FAILED
                    doc.error_message = str(e)[:1000]
                    doc.retry_count += 1
            logger.error("ingestion.failed", doc_id=str(doc_id), error=str(e))
            raise

    async def _run_update(
        self, document_id: str, graph_client: GraphClient
    ) -> None:
        """Update: delete old artifacts and re-process from scratch."""
        async with get_db_session() as db:
            doc = await self._get_document(db, document_id)
            if doc is None:
                logger.error("ingestion.document_not_found", doc_id=document_id)
                return

            vector_store = PgVectorStore(db)
            await vector_store.delete_by_document(str(doc.id))

        try:
            graph_builder = GraphBuilder()
            await graph_builder.delete_document(document_id)
        except Exception as e:
            logger.warn("ingestion.neo4j_delete_skipped", error=str(e))

        await self._run_create(document_id, graph_client)

    async def _run_delete(self, document_id: str) -> None:
        """Delete: remove all indexed artifacts, keep document as tombstone."""
        async with get_db_session() as db:
            doc = await self._get_document(db, document_id)
            if doc is None:
                logger.error("ingestion.document_not_found", doc_id=document_id)
                return

            doc.status = ProcessingStatus.DELETING
            await db.flush()

            vector_store = PgVectorStore(db)
            await vector_store.delete_by_document(str(doc.id))

            blob_path = doc.blob_path
            doc.status = ProcessingStatus.DELETED

        # Neo4j — best-effort
        try:
            graph_builder = GraphBuilder()
            await graph_builder.delete_document(document_id)
        except Exception as e:
            logger.warn("ingestion.neo4j_delete_skipped", error=str(e))

        # Blob — best-effort
        if blob_path:
            blob_store = AzureBlobStore()
            try:
                await blob_store.delete(blob_path)
            except Exception as e:
                logger.warn("ingestion.blob_delete_failed", error=str(e))
            finally:
                await blob_store.close()

        logger.info("ingestion.deleted", doc_id=document_id)

    async def _run_refresh_permissions(
        self, document_id: str, graph_client: GraphClient
    ) -> None:
        """Lightweight RBAC refresh — no content re-processing."""
        async with get_db_session() as db:
            doc = await self._get_document(db, document_id)
            if doc is None:
                logger.error("ingestion.document_not_found", doc_id=document_id)
                return

            resolver = PermissionResolver(graph_client, db)
            await resolver.resolve_and_store(doc)

        from src.storage.cache import invalidate_rbac_cache_for_document
        await invalidate_rbac_cache_for_document(document_id)

        logger.info("ingestion.permissions_refreshed", doc_id=document_id)

    async def _get_document(
        self, db, document_id: str
    ) -> Document | None:
        result = await db.execute(
            select(Document).where(Document.id == uuid.UUID(document_id))
        )
        return result.scalar_one_or_none()
