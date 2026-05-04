"""doctr OCR provider — local/offline fallback for Azure Document Intelligence."""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger()


class DoctrOCRProvider:
    """
    Local OCR provider using doctr (Document Text Recognition).

    Used as a fallback when:
    - Azure Document Intelligence is unavailable (offline/dev)
    - Cost-sensitive scenarios
    - Air-gapped deployments

    Supports: PDF and image files (PNG, JPG, TIFF).
    Note: doctr does not natively support PPTX/DOCX/XLSX — use file parsers for those.

    Install: pip install python-doctr[torch]
    """

    def __init__(self) -> None:
        self._ocr_model = None  # Lazy loaded

    def _load_model(self):
        """Lazy-load the doctr OCR pipeline."""
        if self._ocr_model is None:
            try:
                from doctr.models import ocr_predictor
                self._ocr_model = ocr_predictor(pretrained=True)
                logger.info("doctr_ocr.model_loaded")
            except ImportError as e:
                raise RuntimeError(
                    "doctr is not installed. "
                    "Run: pip install python-doctr[torch]\n"
                    f"Original error: {e}"
                ) from e
        return self._ocr_model

    async def extract_text(
        self,
        file_bytes: bytes,
        file_type: str = "pdf",
    ) -> str:
        """
        Extract text from a document using doctr OCR.

        Args:
            file_bytes: Raw file bytes.
            file_type: File type hint (pdf, png, jpg, tiff).

        Returns:
            Extracted text as a plain string.
        """
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._extract_sync,
            file_bytes,
            file_type,
        )

    def _extract_sync(self, file_bytes: bytes, file_type: str) -> str:
        """Synchronous extraction (runs in thread pool)."""
        import io

        model = self._load_model()

        try:
            if file_type == "pdf":
                return self._extract_from_pdf(file_bytes, model)
            else:
                return self._extract_from_image(file_bytes, model)
        except Exception as e:
            logger.error("doctr_ocr.extraction_failed", file_type=file_type, error=str(e))
            raise

    def _extract_from_pdf(self, file_bytes: bytes, model) -> str:
        """Extract text from a PDF using doctr."""
        try:
            from doctr.io import DocumentFile
            doc = DocumentFile.from_pdf(file_bytes)
        except Exception as e:
            logger.error("doctr_ocr.pdf_load_failed", error=str(e))
            raise

        result = model(doc)
        return self._result_to_text(result)

    def _extract_from_image(self, file_bytes: bytes, model) -> str:
        """Extract text from an image file using doctr."""
        try:
            from doctr.io import DocumentFile
            import numpy as np
            from PIL import Image
            import io

            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            img_array = np.array(image)
            doc = DocumentFile.from_images([img_array])
        except Exception as e:
            logger.error("doctr_ocr.image_load_failed", error=str(e))
            raise

        result = model(doc)
        return self._result_to_text(result)

    @staticmethod
    def _result_to_text(result) -> str:
        """Convert doctr OCRResult to plain text, preserving paragraph structure."""
        pages_text: list[str] = []

        for page in result.pages:
            page_lines: list[str] = []
            for block in page.blocks:
                block_lines: list[str] = []
                for line in block.lines:
                    line_words = [word.value for word in line.words]
                    if line_words:
                        block_lines.append(" ".join(line_words))
                if block_lines:
                    page_lines.append("\n".join(block_lines))
            if page_lines:
                pages_text.append("\n\n".join(page_lines))

        return "\n\n---\n\n".join(pages_text)

    async def extract_with_layout(
        self,
        file_bytes: bytes,
        file_type: str = "pdf",
    ) -> dict:
        """
        Extract text with layout information (bounding boxes, confidence scores).

        Returns:
            Dict with:
            - text: full extracted text
            - pages: list of page dicts with blocks/lines/words
            - confidence: average word confidence score
        """
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._extract_layout_sync,
            file_bytes,
            file_type,
        )

    def _extract_layout_sync(self, file_bytes: bytes, file_type: str) -> dict:
        """Synchronous layout extraction."""
        model = self._load_model()

        if file_type == "pdf":
            from doctr.io import DocumentFile
            doc = DocumentFile.from_pdf(file_bytes)
        else:
            from doctr.io import DocumentFile
            import numpy as np
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            doc = DocumentFile.from_images([np.array(image)])

        result = model(doc)

        pages_data = []
        total_words = 0
        total_confidence = 0.0

        for page_idx, page in enumerate(result.pages):
            page_blocks = []
            for block in page.blocks:
                block_data = {"lines": []}
                for line in block.lines:
                    line_data = {"words": []}
                    for word in line.words:
                        line_data["words"].append({
                            "value": word.value,
                            "confidence": float(word.confidence),
                            "geometry": word.geometry,
                        })
                        total_words += 1
                        total_confidence += float(word.confidence)
                    block_data["lines"].append(line_data)
                page_blocks.append(block_data)
            pages_data.append({"page": page_idx + 1, "blocks": page_blocks})

        avg_confidence = (total_confidence / total_words) if total_words > 0 else 0.0

        return {
            "text": self._result_to_text(result),
            "pages": pages_data,
            "confidence": avg_confidence,
            "provider": "doctr",
        }
