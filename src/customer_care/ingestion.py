"""Customer Care ingestion pipeline — download → chunk → embed → graph → RBAC into CC tables."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.chunking.adaptive_chunker import AdaptiveSemanticChunker
from src.connectors.graph_client import GraphClient, GraphClientFactory, GraphNotFoundError
from src.connectors.permissions import PermissionResolver
from src.customer_care.models import (
    CCDocument,
    CCDocumentPermission,
    CCDocumentUserAccess,
)
from src.customer_care.vector_store import CCVectorStore
from src.embedding.azure_openai import AzureOpenAIEmbedder
from src.knowledge_graph.builder import GraphBuilder
from src.models.enums import ProcessingStatus
from src.processing.pipeline import DocumentProcessingPipeline
from src.storage.blob_store import AzureBlobStore
from src.storage.db import get_db_session

logger = structlog.get_logger()


class CCPermissionResolver(PermissionResolver):
    """
    Permission resolver for CC documents.

    Subclasses PermissionResolver to reuse Graph API calls (_fetch_permissions,
    _expand_group) but writes to cc_document_permissions / cc_document_user_access.
    """

    async def resolve_and_store(self, document: CCDocument) -> int:  # type: ignore[override]
        """Resolve permissions for a CC document and store in CC RBAC tables."""
        permissions = await self._fetch_permissions(
            document.drive_id, document.drive_item_id
        )

        # Clear existing CC permissions for this document
        await self.db.execute(
            delete(CCDocumentPermission).where(
                CCDocumentPermission.document_id == document.id
            )
        )
        await self.db.execute(
            delete(CCDocumentUserAccess).where(
                CCDocumentUserAccess.document_id == document.id
            )
        )

        all_user_ids: set[str] = set()

        for perm in permissions:
            granted_to = perm.get("grantedToV2") or perm.get("grantedTo") or {}
            roles = perm.get("roles", ["read"])
            role = roles[0] if roles else "read"

            user = granted_to.get("user")
            if user and user.get("id"):
                user_id = user["id"]
                self.db.add(
                    CCDocumentPermission(
                        document_id=document.id,
                        principal_type="user",
                        principal_id=user_id,
                        role=role,
                    )
                )
                all_user_ids.add(user_id)

            group = granted_to.get("group")
            if group and group.get("id"):
                group_id = group["id"]
                self.db.add(
                    CCDocumentPermission(
                        document_id=document.id,
                        principal_type="group",
                        principal_id=group_id,
                        role=role,
                    )
                )
                member_ids = await self._expand_group(group_id)
                all_user_ids.update(member_ids)

            site_user = granted_to.get("siteUser")
            if site_user and site_user.get("id"):
                login = site_user.get("loginName", site_user["id"])
                self.db.add(
                    CCDocumentPermission(
                        document_id=document.id,
                        principal_type="site_user",
                        principal_id=login,
                        role=role,
                    )
                )

        for user_id in all_user_ids:
            self.db.add(
                CCDocumentUserAccess(
                    document_id=document.id,
                    user_id=user_id,
                )
            )

        await self.db.flush()

        logger.info(
            "cc_permissions.resolved",
            document_id=str(document.id),
            permission_count=len(permissions),
            user_access_count=len(all_user_ids),
        )
        return len(all_user_ids)


def _extract_category(parent_path: str | None) -> str | None:
    """Extract the last folder segment from a SharePoint parent path as the CC category."""
    if not parent_path:
        return None
    # SharePoint paths look like: /drives/<id>/root:/Folder/SubFolder
    # Strip the drive root prefix and take the last segment
    path = parent_path.rstrip("/")
    if ":" in path:
        path = path.split(":", 1)[-1]
    segments = [s for s in path.split("/") if s]
    return segments[-1] if segments else None


class CCIngestionPipeline:
    """
    Full ingestion pipeline for Customer Care documents.

    Mirrors IngestionPipeline (src/processing/ingestion.py) but:
    - Reads/writes CCDocument / CCDocumentChunk models
    - Uses CCVectorStore for pgvector indexing
    - Uses CCPermissionResolver for RBAC
    - Extracts category from parent_path (folder segment)
    - Calls GraphBuilder (unchanged — Neo4j is table-agnostic)

    Operations: create, update, delete, refresh_permissions
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
            async with get_db_session() as db:
                doc = await self._get_document(db, document_id)
                if doc is None:
                    logger.error("cc_ingestion.document_not_found", doc_id=document_id)
                    return

                if operation == "create":
                    await self._run_create(db, doc, graph_client)
                elif operation == "update":
                    await self._run_update(db, doc, graph_client)
                elif operation == "delete":
                    await self._run_delete(db, doc)
                elif operation == "refresh_permissions":
                    await self._run_refresh_permissions(db, doc, graph_client)
                else:
                    logger.error("cc_ingestion.unknown_operation", operation=operation)
        finally:
            await graph_client.close()

    async def _run_create(
        self, db: AsyncSession, doc: CCDocument, graph_client: GraphClient
    ) -> None:
        """Full ingestion: download → stage → process → chunk → embed → graph → RBAC."""
        try:
            # 1. Download
            doc.status = ProcessingStatus.DOWNLOADING
            doc.processing_started_at = datetime.now(timezone.utc)
            await db.flush()

            download_url = await graph_client.get_download_url(doc.drive_id, doc.drive_item_id)
            file_bytes = await graph_client.download_file(download_url)

            content_hash = hashlib.sha256(file_bytes).hexdigest()
            if doc.content_hash == content_hash:
                logger.info("cc_ingestion.content_unchanged", doc_id=str(doc.id))
                doc.status = ProcessingStatus.COMPLETED
                return
            doc.content_hash = content_hash

            # Derive category from folder path
            doc.category = _extract_category(doc.parent_path)
            doc.policy_url = doc.sharepoint_url

            # 2. Stage to Blob Storage
            blob_store = AzureBlobStore()
            blob_path = f"cc_staging/{doc.drive_id}/{doc.drive_item_id}/{doc.name}"
            try:
                await blob_store.upload(blob_path, file_bytes)
                doc.blob_path = blob_path
            except Exception as e:
                logger.warn("cc_ingestion.blob_upload_failed", error=str(e))
            finally:
                await blob_store.close()

            # 3. Process (parse + OCR + metadata + summary + entities)
            doc.status = ProcessingStatus.PROCESSING
            await db.flush()

            pipeline = DocumentProcessingPipeline()
            graph_meta = {
                "created_by": doc.created_by,
                "modified_by": doc.modified_by,
                "sharepoint_url": doc.sharepoint_url,
                "parent_path": doc.parent_path,
            }
            processed = await pipeline.process(
                file_bytes=file_bytes,
                filename=doc.name,
                file_type=doc.file_type,
                graph_metadata=graph_meta,
            )

            doc.summary = processed.summary
            doc.language = processed.language

            # 4. Chunk
            doc.status = ProcessingStatus.CHUNKING
            await db.flush()

            chunker = AdaptiveSemanticChunker()
            chunks = chunker.chunk(
                text=processed.text,
                file_type=doc.file_type,
                metadata={
                    "summary_prefix": processed.summary[:300] if processed.summary else "",
                    "section_summaries": processed.section_summaries,
                },
                parsed_doc=processed.parsed,
            )

            # 5. Embed
            doc.status = ProcessingStatus.EMBEDDING
            await db.flush()

            embedder = AzureOpenAIEmbedder()
            texts_to_embed = [c.content_with_context for c in chunks]
            embeddings = await embedder.embed_texts(texts_to_embed)

            # 6. Index in cc_document_chunks (pgvector)
            doc.status = ProcessingStatus.INDEXING
            await db.flush()

            vector_store = CCVectorStore(db)
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

            # 7. Index in Neo4j graph — use actual pgvector chunk IDs
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
                doc_id=str(doc.id),
                title=doc.name,
                department=doc.category,    # category maps to department slot in graph
                content_type="customer_care_policy",
                access_level=None,
                summary=doc.summary,
                sharepoint_url=doc.sharepoint_url,
                file_type=doc.file_type,
                created_at=doc.created_at.isoformat() if doc.created_at else "",
                chunks=graph_chunks,
                entities=processed.entities,
                topics=processed.topics,
            )

            # 8. RBAC permissions
            permission_resolver = CCPermissionResolver(graph_client, db)
            await permission_resolver.resolve_and_store(doc)

            # 9. Mark complete
            doc.status = ProcessingStatus.COMPLETED
            doc.processing_completed_at = datetime.now(timezone.utc)
            doc.error_message = None

            logger.info(
                "cc_ingestion.completed",
                doc_id=str(doc.id),
                name=doc.name,
                category=doc.category,
                chunks=len(chunks),
            )

        except Exception as e:
            doc.status = ProcessingStatus.FAILED
            doc.error_message = str(e)[:1000]
            doc.retry_count += 1
            logger.error("cc_ingestion.failed", doc_id=str(doc.id), error=str(e))
            raise

    async def _run_update(
        self, db: AsyncSession, doc: CCDocument, graph_client: GraphClient
    ) -> None:
        """Update: delete old CC artifacts and re-process."""
        vector_store = CCVectorStore(db)
        await vector_store.delete_by_document(str(doc.id))

        graph_builder = GraphBuilder()
        await graph_builder.delete_document(str(doc.id))

        await self._run_create(db, doc, graph_client)

    async def _run_delete(self, db: AsyncSession, doc: CCDocument) -> None:
        """Delete: remove all CC artifacts, keep document as tombstone."""
        doc.status = ProcessingStatus.DELETING
        await db.flush()

        vector_store = CCVectorStore(db)
        await vector_store.delete_by_document(str(doc.id))

        graph_builder = GraphBuilder()
        await graph_builder.delete_document(str(doc.id))

        if doc.blob_path:
            blob_store = AzureBlobStore()
            try:
                await blob_store.delete(doc.blob_path)
            except Exception as e:
                logger.warn("cc_ingestion.blob_delete_failed", error=str(e))
            finally:
                await blob_store.close()

        doc.status = ProcessingStatus.DELETED
        logger.info("cc_ingestion.deleted", doc_id=str(doc.id), name=doc.name)

    async def _run_refresh_permissions(
        self, db: AsyncSession, doc: CCDocument, graph_client: GraphClient
    ) -> None:
        """Lightweight RBAC refresh — no content re-processing."""
        resolver = CCPermissionResolver(graph_client, db)
        await resolver.resolve_and_store(doc)
        logger.info("cc_ingestion.permissions_refreshed", doc_id=str(doc.id))

    async def _get_document(
        self, db: AsyncSession, document_id: str
    ) -> CCDocument | None:
        result = await db.execute(
            select(CCDocument).where(CCDocument.id == uuid.UUID(document_id))
        )
        return result.scalar_one_or_none()
