"""PDF parser — PyMuPDF for text extraction, Azure DI for OCR when needed."""

from __future__ import annotations

import asyncio
import io

import structlog

from src.processing.parsers.base import DocumentParser, ParsedDocument

logger = structlog.get_logger()


class PDFParser(DocumentParser):
    """
    PDF parser using PyMuPDF.

    Strategy:
    1. Check if PDF has extractable text layer (via PyMuPDF)
    2. If yes: extract text directly (fast, free)
    3. If no: delegate to Azure Document Intelligence OCR
    """

    async def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_bytes, filename)

    def _parse_sync(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        import pymupdf

        doc = pymupdf.open(stream=file_bytes, filetype="pdf")

        pages_text = []
        sections = []
        has_text_layer = False

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")

            if text.strip():
                has_text_layer = True

            pages_text.append(text)

            # Extract text blocks for structure detection
            blocks = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
            for block in blocks.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            size = span.get("size", 12)
                            text_content = span.get("text", "").strip()
                            if text_content and size > 14:
                                sections.append({
                                    "heading": text_content,
                                    "level": 1 if size > 18 else 2,
                                    "page": page_num + 1,
                                })

        full_text = "\n\n".join(pages_text)
        word_count = len(full_text.split())

        logger.info(
            "pdf_parser.parsed",
            filename=filename,
            pages=len(doc),
            has_text_layer=has_text_layer,
            words=word_count,
        )

        doc.close()

        return ParsedDocument(
            text=full_text,
            sections=sections,
            page_count=len(pages_text),
            word_count=word_count,
            file_properties={
                "has_text_layer": has_text_layer,
            },
        )
