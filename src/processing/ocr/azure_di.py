"""Azure Document Intelligence OCR provider (prebuilt-layout model)."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import (
    AnalyzeDocumentRequest,
    AnalyzeResult,
    DocumentAnalysisFeature,
)
from azure.core.credentials import AzureKeyCredential

from config.settings import get_settings

logger = structlog.get_logger()


@dataclass
class OCRResult:
    """Structured OCR output."""

    markdown: str = ""
    tables: list[dict] = field(default_factory=list)
    paragraphs: list[dict] = field(default_factory=list)
    page_count: int = 0
    word_count: int = 0
    languages: list[str] = field(default_factory=list)


class AzureDocumentIntelligenceOCR:
    """
    Primary OCR provider using Azure Document Intelligence prebuilt-layout model.

    Supports PDF, PPTX, DOCX, XLSX natively. Extracts text, tables, figures,
    and paragraph roles (title, sectionHeading, etc.) as Markdown output.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = DocumentIntelligenceClient(
            endpoint=self.settings.OCR_AZURE_ENDPOINT,
            credential=AzureKeyCredential(self.settings.OCR_AZURE_KEY),
        )

    async def analyze(self, file_bytes: bytes, file_type: str) -> OCRResult:
        """
        Analyze a document and extract structured content.

        Args:
            file_bytes: Raw file content.
            file_type: Extension without dot (pdf, docx, pptx, xlsx).

        Returns:
            OCRResult with markdown text, tables, and metadata.
        """
        logger.info("ocr.analyzing", file_type=file_type, size_bytes=len(file_bytes))

        # OCR_HIGH_RESOLUTION is only valid for PDF/image files, not Office formats
        office_types = {"pptx", "docx", "xlsx"}
        features = [] if file_type.lower() in office_types else [DocumentAnalysisFeature.OCR_HIGH_RESOLUTION]

        # Files > 6 MB must go via blob URL (Azure DI inline limit)
        if len(file_bytes) > 6 * 1024 * 1024:
            return await self._analyze_via_blob(file_bytes, file_type, features)

        import asyncio
        return await asyncio.to_thread(
            self._analyze_sync_bytes, file_bytes, features
        )

    async def _analyze_via_blob(self, file_bytes: bytes, file_type: str, features: list) -> OCRResult:
        """Upload to blob, analyze via url_source, then delete the temp blob."""
        import asyncio
        import uuid

        from src.storage.blob_store import AzureBlobStore

        blob_store = AzureBlobStore()
        blob_path = f"ocr-temp/{uuid.uuid4()}.{file_type}"
        try:
            await blob_store.upload(blob_path, file_bytes)
            sas_url = await blob_store.get_sas_url(blob_path, expiry_hours=1)
            logger.info("ocr.analyzing_via_blob", blob_path=blob_path, size_mb=len(file_bytes) // 1024 // 1024)
            result = await asyncio.to_thread(self._analyze_sync_url, sas_url, features)
            return result
        finally:
            try:
                await blob_store.delete(blob_path)
            except Exception:
                pass

    def _analyze_sync_bytes(self, file_bytes: bytes, features: list) -> OCRResult:
        """Analyze small files inline via bytes_source."""
        poller = self.client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=AnalyzeDocumentRequest(bytes_source=file_bytes),
            output_content_format="markdown",
            features=features or None,
        )
        return self._parse_result(poller.result())

    def _analyze_sync_url(self, url: str, features: list) -> OCRResult:
        """Analyze large files via url_source."""
        poller = self.client.begin_analyze_document(
            model_id="prebuilt-layout",
            body=AnalyzeDocumentRequest(url_source=url),
            output_content_format="markdown",
            features=features or None,
        )
        return self._parse_result(poller.result())

    def _parse_result(self, result: AnalyzeResult) -> OCRResult:
        """Parse an AnalyzeResult into OCRResult."""
        # Extract markdown content
        markdown = result.content or ""

        # Extract tables
        tables = []
        if result.tables:
            for table in result.tables:
                table_data = {
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "cells": [],
                }
                if table.cells:
                    for cell in table.cells:
                        table_data["cells"].append({
                            "row": cell.row_index,
                            "column": cell.column_index,
                            "content": cell.content,
                            "kind": cell.kind if hasattr(cell, "kind") else "content",
                        })
                tables.append(table_data)

        # Extract paragraphs with roles
        paragraphs = []
        if result.paragraphs:
            for para in result.paragraphs:
                paragraphs.append({
                    "content": para.content,
                    "role": para.role if hasattr(para, "role") else None,
                })

        # Page and word count
        page_count = len(result.pages) if result.pages else 0
        word_count = sum(
            len(page.words) for page in (result.pages or []) if page.words
        )

        # Languages detected (attribute may not exist for Office file types)
        languages = list({
            lang.locale
            for page in (result.pages or [])
            for lang in (getattr(page, "languages", None) or [])
            if lang.locale
        })

        logger.info(
            "ocr.completed",
            pages=page_count,
            words=word_count,
            tables=len(tables),
            paragraphs=len(paragraphs),
        )

        return OCRResult(
            markdown=markdown,
            tables=tables,
            paragraphs=paragraphs,
            page_count=page_count,
            word_count=word_count,
            languages=languages,
        )
