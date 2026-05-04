"""
Local folder ingestion — no SharePoint credentials required.

Usage:
    python scripts/local_ingest.py /path/to/folder
    python scripts/local_ingest.py /path/to/folder --glob "**/*.pptx"

Reads .pptx / .docx / .pdf / .xlsx files from a local folder and runs them
through the full processing pipeline (parse → chunk → embed → pgvector → Neo4j),
skipping only the SharePoint download and RBAC steps.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.chunking.adaptive_chunker import AdaptiveSemanticChunker  # noqa: E402
from src.embedding.azure_openai import AzureOpenAIEmbedder  # noqa: E402
from src.processing.pipeline import DocumentProcessingPipeline  # noqa: E402

logger = structlog.get_logger()

SUPPORTED_EXTENSIONS = {".pptx", ".docx", ".pdf", ".xlsx", ".xlsb"}


async def ingest_file(
    file_path: Path,
    pipeline: DocumentProcessingPipeline | None = None,
    chunker: AdaptiveSemanticChunker | None = None,
    embedder: AzureOpenAIEmbedder | None = None,
    force: bool = False,
    ocr_method: str = "hybrid",
) -> None:
    """Ingest a single local file through the pipeline.

    Heavy processing (parse, vision, summarize, chunk, embed) runs outside
    any DB session to avoid connection timeouts on long-running files.
    DB session is opened only for the fast read/write operations.

    Args:
        force: Re-ingest even if the file hash hasn't changed.
    """
    from sqlalchemy import select

    from src.knowledge_graph.builder import GraphBuilder
    from src.models.document import Document, DocumentUserAccess
    from src.models.enums import ProcessingStatus
    from src.storage.db import get_db_session
    from src.storage.vector_store import PgVectorStore

    # Reuse shared instances or create per-call (for standalone use)
    if pipeline is None:
        pipeline = DocumentProcessingPipeline()
    if chunker is None:
        chunker = AdaptiveSemanticChunker()
    if embedder is None:
        embedder = AzureOpenAIEmbedder()

    file_type = file_path.suffix.lstrip(".")
    file_bytes = file_path.read_bytes()
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Stable fake SharePoint IDs derived from the file path so re-runs are idempotent
    stable_id = hashlib.md5(str(file_path.resolve()).encode()).hexdigest()
    fake_drive_id = f"local-drive-{stable_id[:12]}"
    fake_item_id = f"local-item-{stable_id[12:24]}"
    fake_site_id = "local-site-00000000"

    # ── Phase 1: Quick DB check — create or find document ──
    async with get_db_session() as db:
        result = await db.execute(
            select(Document).where(
                Document.drive_id == fake_drive_id,
                Document.drive_item_id == fake_item_id,
                Document.extraction_method == ocr_method,
            )
        )
        doc = result.scalar_one_or_none()

        if doc is None:
            doc = Document(
                drive_id=fake_drive_id,
                drive_item_id=fake_item_id,
                site_id=fake_site_id,
                name=file_path.name,
                file_type=file_type,
                size_bytes=len(file_bytes),
                sharepoint_url=f"local://{file_path.resolve()}",
                parent_path=str(file_path.parent),
                status=ProcessingStatus.PROCESSING,
                content_hash=content_hash,
                extraction_method=ocr_method,
                processing_started_at=datetime.now(timezone.utc),
            )
            db.add(doc)
            await db.flush()
            logger.info("local_ingest.created_doc", name=file_path.name, doc_id=str(doc.id))
        else:
            if doc.content_hash == content_hash and not force:
                logger.info("local_ingest.unchanged_skipping", name=file_path.name)
                return
            logger.info("local_ingest.updating", name=file_path.name, doc_id=str(doc.id))
            vector_store = PgVectorStore(db)
            await vector_store.delete_by_document(str(doc.id))
            try:
                graph_builder = GraphBuilder()
                await graph_builder.delete_document(str(doc.id))
            except Exception:
                pass
            doc.content_hash = content_hash
            doc.status = ProcessingStatus.PROCESSING
            doc.processing_started_at = datetime.now(timezone.utc)

        # Capture IDs for use outside session
        doc_id = doc.id
        doc_sharepoint_url = doc.sharepoint_url
        doc_parent_path = doc.parent_path
        doc_created_at = doc.created_at

    # ── Phase 2: Heavy processing — NO DB session open ──
    # (parse → vision → summarize → chunk → embed — can take 5-10+ minutes)
    processed = await pipeline.process(
        file_bytes=file_bytes,
        filename=file_path.name,
        file_type=file_type,
        graph_metadata={
            "created_by": None,
            "modified_by": None,
            "sharepoint_url": doc_sharepoint_url,
            "parent_path": doc_parent_path,
        },
        extraction_method=ocr_method,
    )

    chunks = chunker.chunk(
        text=processed.text,
        file_type=file_type,
        metadata={
            "summary_prefix": processed.summary[:300] if processed.summary else "",
            "section_summaries": processed.section_summaries,
        },
        parsed_doc=processed.parsed,
    )

    embeddings = await embedder.embed_texts([c.content_with_context for c in chunks])

    # ── Phase 3: Fast DB write — fresh session, won't timeout ──
    async with get_db_session() as db:
        # Re-fetch the document in this session
        result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one()

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
                "extraction_method": ocr_method,
            }
            for c in chunks
        ]
        chunk_ids = await vector_store.upsert_chunks(doc, chunk_dicts, embeddings)

        # Grant local user access so RBAC-filtered search returns results
        existing_access = await db.execute(
            select(DocumentUserAccess).where(
                DocumentUserAccess.document_id == doc.id,
                DocumentUserAccess.user_id == "anonymous",
            )
        )
        if existing_access.scalar_one_or_none() is None:
            db.add(DocumentUserAccess(document_id=doc.id, user_id="anonymous"))
            await db.flush()

        doc.status = ProcessingStatus.COMPLETED
        doc.processing_completed_at = datetime.now(timezone.utc)
        doc.error_message = None

    # ── Phase 4: Neo4j — outside DB session, best-effort ──
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
            title=file_path.name,
            department=processed.department,
            content_type=processed.content_type,
            access_level=None,
            summary=processed.summary,
            sharepoint_url=doc_sharepoint_url,
            file_type=file_type,
            created_at=doc_created_at.isoformat() if doc_created_at else "",
            chunks=graph_chunks,
            entities=processed.entities,
            topics=processed.topics,
        )
    except Exception as e:
        logger.warn("local_ingest.neo4j_skipped", error=str(e))

    logger.info(
        "local_ingest.completed",
        name=file_path.name,
        doc_id=str(doc_id),
        chunks=len(chunks),
    )


async def clear_all_ingestion() -> None:
    """Delete all ingested documents from PostgreSQL, Neo4j, and blob storage."""
    from sqlalchemy import text

    from src.knowledge_graph.builder import close_neo4j_driver, get_neo4j_driver
    from src.storage.db import get_db_session

    # Clear PostgreSQL (CASCADE removes chunks, permissions, user_access)
    async with get_db_session() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM documents"))
        count = result.scalar()
        await db.execute(text("TRUNCATE documents CASCADE"))
    print(f"Deleted {count} document(s) and all related chunks from PostgreSQL.")

    # Clear Neo4j
    try:
        driver = await get_neo4j_driver()
        async with driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await close_neo4j_driver()
        print("Cleared all nodes from Neo4j.")
    except Exception as e:
        print(f"Neo4j clear skipped: {e}")

    # Clear blob storage containers used by local ingest and OCR
    try:
        from src.storage.blob_store import AzureBlobStore
        blob_store = AzureBlobStore()
        client = await blob_store._get_client()
        container = client.get_container_client(blob_store.settings.BLOB_CONTAINER_NAME)
        deleted = 0
        async for blob in container.list_blobs(name_starts_with="ocr-temp/"):
            await container.delete_blob(blob.name)
            deleted += 1
        print(f"Deleted {deleted} blob(s) from storage.")
    except Exception as e:
        print(f"Blob clear skipped: {e}")


async def ingest_folder(folder: Path, glob_pattern: str = "**/*", force: bool = False, ocr_method: str = "hybrid") -> None:
    files = [
        p for p in folder.glob(glob_pattern)
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        print(f"No supported files found in {folder}")
        return

    print(f"Found {len(files)} file(s) to ingest:")
    for f in files:
        print(f"  {f.relative_to(folder)}")
    print()

    # Create shared instances once — reused across all files
    pipeline = DocumentProcessingPipeline()
    chunker = AdaptiveSemanticChunker()
    embedder = AzureOpenAIEmbedder()

    ok = failed = 0
    for file_path in files:
        try:
            await ingest_file(file_path, pipeline=pipeline, chunker=chunker, embedder=embedder, force=force, ocr_method=ocr_method)
            ok += 1
        except Exception as e:
            logger.error("local_ingest.file_failed", name=file_path.name, error=str(e))
            failed += 1

    print(f"\nDone — {ok} succeeded, {failed} failed.")


async def run(clear: bool, folder: Path | None, glob_pattern: str, force: bool = False, ocr_method: str = "hybrid") -> None:
    """Single async entry point — avoids multiple asyncio.run() calls that break the engine."""
    if clear:
        print("Clearing all previous ingestion data...")
        await clear_all_ingestion()
        print()

    if folder:
        await ingest_folder(folder, glob_pattern=glob_pattern, force=force, ocr_method=ocr_method)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ingest local files into the RAG pipeline.")
    parser.add_argument("folder", nargs="?", help="Path to folder containing files to ingest")
    parser.add_argument(
        "--glob",
        default="**/*",
        help="Glob pattern to filter files (default: **/*)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all previously ingested documents before ingesting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest files even if their content has not changed (bypasses hash check)",
    )
    parser.add_argument(
        "--ocr-method",
        default="hybrid",
        help="OCR/vision extraction method (default: hybrid). "
             "hybrid = Mistral OCR for PPTX text slides + Vision LLM for charts/PDFs. "
             "Other options: 'vision_llm' (full vision), 'mistral_ocr' (full Mistral).",
    )
    args = parser.parse_args()

    folder = None
    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            print(f"Error: {folder} is not a directory")
            sys.exit(1)

    if folder or args.clear:
        asyncio.run(run(args.clear, folder, args.glob, force=args.force, ocr_method=args.ocr_method))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
