"""PDF → PNG page renderer using pymupdf.

Renders each page of a PDF as a high-res PNG image for vision model extraction.
Unlike PPTX, no LibreOffice conversion needed — pymupdf reads PDF natively.
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger()

# DPI for rendering — 200 gives good detail for vision models
RENDER_DPI = 200


async def render_pdf_pages(file_bytes: bytes, filename: str) -> list[bytes]:
    """Render each PDF page as a PNG image.

    Args:
        file_bytes: Raw PDF file content.
        filename: Original filename (for logging).

    Returns:
        List of PNG bytes, one per page in order.
    """
    return await asyncio.to_thread(_render_sync, file_bytes, filename)


def _render_sync(file_bytes: bytes, filename: str) -> list[bytes]:
    import fitz  # pymupdf

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    images: list[bytes] = []
    skipped = 0

    for page_num, page in enumerate(doc, 1):
        try:
            mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
        except Exception:
            # Retry at lower DPI — large pages may exceed memory at full res
            try:
                fallback_dpi = 100
                mat = fitz.Matrix(fallback_dpi / 72, fallback_dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                images.append(pix.tobytes("png"))
                logger.info(
                    "pdf_renderer.fallback_dpi",
                    filename=filename,
                    page=page_num,
                    dpi=fallback_dpi,
                )
            except Exception as e2:
                # Skip this page entirely — append empty bytes as placeholder
                images.append(b"")
                skipped += 1
                logger.warn(
                    "pdf_renderer.page_skipped",
                    filename=filename,
                    page=page_num,
                    error=str(e2),
                )

    doc.close()

    logger.info(
        "pdf_renderer.rendered",
        filename=filename,
        pages=len(images),
        skipped=skipped,
        dpi=RENDER_DPI,
    )
    return images
