"""Abstract OCR/vision extraction interface for pluggable extraction methods.

Concrete implementations:
  - VisionLLMExtractor: GPT-4.1-mini vision (current default)
  - PluggableOCRExtractor: Stub for user-provided OCR service
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.processing.parsers.base import ParsedDocument


class BaseOCRExtractor(ABC):
    """Base class for OCR/vision extraction methods.

    Each implementation takes the raw file bytes plus the parser output
    and enriches the ParsedDocument with extracted visual content.

    For PDFs: populate ``parsed_doc.pages[i]["vision_text"]``
    For PPTX: append extracted content to ``parsed_doc.slides[i]["content"]``
    """

    @abstractmethod
    async def extract(
        self,
        file_bytes: bytes,
        filename: str,
        file_type: str,
        parsed_doc: ParsedDocument,
    ) -> str:
        """Run extraction and enrich parsed_doc in-place.

        Args:
            file_bytes: Raw file content.
            filename: Original filename.
            file_type: Extension without dot (pdf, pptx, etc.).
            parsed_doc: Parser output to enrich with vision/OCR content.

        Returns:
            The full enriched text (parser text + extracted visual content).
        """
        ...


class VisionLLMExtractor(BaseOCRExtractor):
    """GPT-4.1-mini vision extraction — the current default method.

    Renders each page/slide as PNG and sends to the vision model for
    structured chart/graph/table extraction.
    """

    async def extract(
        self,
        file_bytes: bytes,
        filename: str,
        file_type: str,
        parsed_doc: ParsedDocument,
    ) -> str:
        from src.processing.ocr.vision_extractor import VisionSlideExtractor

        if file_type == "pdf":
            return await self._extract_pdf(file_bytes, filename, parsed_doc)
        elif file_type == "pptx":
            return await self._extract_pptx(file_bytes, filename, parsed_doc)
        return parsed_doc.text

    async def _extract_pdf(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        from src.processing.ocr.pdf_renderer import render_pdf_pages
        from src.processing.ocr.vision_extractor import VisionSlideExtractor

        import structlog
        logger = structlog.get_logger()

        page_images = await render_pdf_pages(file_bytes, filename)
        logger.info("vision_llm.pdf_pages_rendered", filename=filename, pages=len(page_images))

        page_texts = parsed_doc.text.split("\n\n") if parsed_doc.text else []

        vision_pages = []
        for i, img_bytes in enumerate(page_images):
            parser_text = page_texts[i] if i < len(page_texts) else ""
            vision_pages.append({
                "index": i + 1,
                "image": img_bytes,
                "text": parser_text,
            })

        extractor = VisionSlideExtractor()
        vision_results = await extractor.extract_batch(vision_pages)

        enriched_parts = []
        for i in range(len(page_images)):
            page_num = i + 1
            parser_text = page_texts[i].strip() if i < len(page_texts) else ""
            vision_text = vision_results.get(page_num, "")
            if vision_text.strip().upper() == "NONE":
                vision_text = ""

            parsed_doc.pages.append({
                "index": page_num,
                "text": parser_text,
                "vision_text": vision_text,
            })

            parts = []
            if parser_text:
                parts.append(parser_text)
            if vision_text:
                parts.append(vision_text)
            enriched_parts.append("\n\n".join(parts))

        logger.info(
            "vision_llm.pdf_extraction_done",
            filename=filename,
            total_pages=len(page_images),
            pages_enriched=len(vision_results),
        )
        return "\n\n".join(enriched_parts)

    async def _extract_pptx(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        from src.processing.ocr.slide_renderer import render_slides
        from src.processing.ocr.vision_extractor import VisionSlideExtractor

        import structlog
        logger = structlog.get_logger()

        slide_images = await render_slides(file_bytes, filename)
        logger.info("vision_llm.slides_rendered", filename=filename, slides=len(slide_images))

        vision_slides = []
        for i, img_bytes in enumerate(slide_images):
            slide_data = parsed_doc.slides[i] if i < len(parsed_doc.slides) else {}
            parser_text = "\n".join(slide_data.get("content", []))
            vision_slides.append({
                "index": i + 1,
                "image": img_bytes,
                "text": parser_text,
            })

        extractor = VisionSlideExtractor()
        vision_results = await extractor.extract_batch(vision_slides)

        for slide in parsed_doc.slides:
            vision_text = vision_results.get(slide["index"], "")
            if vision_text and vision_text.strip().upper() != "NONE":
                slide["content"].append(vision_text)

        text_parts = []
        for slide in parsed_doc.slides:
            slide_text = f"--- Slide {slide['index']} ---\n"
            if slide.get("title"):
                slide_text += f"Title: {slide['title']}\n"
            for content in slide.get("content", []):
                slide_text += f"{content}\n"
            if slide.get("notes"):
                slide_text += f"Speaker Notes: {slide['notes']}\n"
            text_parts.append(slide_text)

        logger.info(
            "vision_llm.pptx_extraction_done",
            filename=filename,
            total_slides=len(slide_images),
            slides_enriched=len(vision_results),
        )
        return "\n\n".join(text_parts)


class MistralOCRExtractor(BaseOCRExtractor):
    """Mistral Document AI 2512 OCR via Azure AI Foundry.

    Sends the full PDF as base64 to the Mistral OCR endpoint.
    Returns per-page markdown which maps to ParsedDocument.pages.
    For PPTX: converts to PDF first via LibreOffice, then sends to Mistral.
    """

    async def extract(
        self,
        file_bytes: bytes,
        filename: str,
        file_type: str,
        parsed_doc: ParsedDocument,
    ) -> str:
        if file_type == "pdf":
            return await self._extract_pdf(file_bytes, filename, parsed_doc)
        elif file_type == "pptx":
            return await self._extract_pptx(file_bytes, filename, parsed_doc)
        return parsed_doc.text

    # Azure AI Foundry Mistral Document AI has a 30-page limit per request
    MAX_PAGES_PER_REQUEST = 25  # Use 25 for safety margin

    async def _call_mistral_ocr(self, file_bytes: bytes, mime_type: str = "application/pdf") -> list[dict]:
        """Call Mistral OCR API, auto-splitting large PDFs into batches.

        Azure AI Foundry limits Mistral Document AI to ~30 pages per request.
        PDFs exceeding that are split into batches and results are merged.
        """
        import asyncio

        import structlog

        logger = structlog.get_logger()

        # Check page count and split if needed
        page_count = self._count_pdf_pages(file_bytes)
        if page_count > self.MAX_PAGES_PER_REQUEST:
            logger.info(
                "mistral_ocr.splitting_pdf",
                total_pages=page_count,
                batch_size=self.MAX_PAGES_PER_REQUEST,
            )
            pdf_batches = self._split_pdf(file_bytes, self.MAX_PAGES_PER_REQUEST)
            all_pages: list[dict] = []
            page_offset = 0
            for batch_idx, batch_bytes in enumerate(pdf_batches):
                batch_page_count = self._count_pdf_pages(batch_bytes)
                logger.info(
                    "mistral_ocr.batch",
                    batch=batch_idx + 1,
                    total_batches=len(pdf_batches),
                    pages_in_batch=batch_page_count,
                )
                batch_pages = await self._call_mistral_ocr_single(batch_bytes, mime_type)
                # Re-index pages to global page numbers
                for page in batch_pages:
                    page["index"] = page.get("index", 0) + page_offset
                all_pages.extend(batch_pages)
                page_offset += batch_page_count
            logger.info("mistral_ocr.done", pages=len(all_pages), batches=len(pdf_batches))
            return all_pages
        else:
            return await self._call_mistral_ocr_single(file_bytes, mime_type)

    async def _call_mistral_ocr_single(self, file_bytes: bytes, mime_type: str = "application/pdf") -> list[dict]:
        """Send a single PDF (must be ≤ MAX_PAGES_PER_REQUEST) to the Mistral OCR API."""
        import base64

        import asyncio

        import httpx
        import structlog

        from config.settings import get_settings

        logger = structlog.get_logger()
        settings = get_settings()

        if not settings.MISTRAL_OCR_ENDPOINT or not settings.MISTRAL_OCR_API_KEY:
            raise RuntimeError(
                "Mistral OCR not configured. Set MISTRAL_OCR_ENDPOINT and "
                "MISTRAL_OCR_API_KEY in .env"
            )

        b64_data = base64.b64encode(file_bytes).decode()
        data_uri = f"data:{mime_type};base64,{b64_data}"

        payload = {
            "model": settings.MISTRAL_OCR_MODEL,
            "document": {
                "type": "document_url",
                "document_url": data_uri,
            },
            "include_image_base64": False,
        }

        url = settings.MISTRAL_OCR_ENDPOINT.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.MISTRAL_OCR_API_KEY}",
        }

        b64_size_mb = len(b64_data) / (1024 * 1024)
        logger.info("mistral_ocr.calling", url=url, size_bytes=len(file_bytes), base64_mb=round(b64_size_mb, 2))

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code in (408, 429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 5 * attempt
                    logger.warn(
                        "mistral_ocr.retrying",
                        status=response.status_code,
                        attempt=attempt,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                if response.status_code != 200:
                    logger.error(
                        "mistral_ocr.api_error",
                        status=response.status_code,
                        body=response.text[:2000],
                    )
                    response.raise_for_status()
                break

        result = response.json()
        pages = result.get("pages", [])
        logger.info("mistral_ocr.done", pages=len(pages))
        return pages

    @staticmethod
    def _count_pdf_pages(pdf_bytes: bytes) -> int:
        """Return the number of pages in a PDF."""
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count

    @staticmethod
    def _split_pdf(pdf_bytes: bytes, max_pages: int) -> list[bytes]:
        """Split a PDF into multiple PDFs of at most max_pages each."""
        import fitz
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        total = len(src)
        parts: list[bytes] = []
        for start in range(0, total, max_pages):
            end = min(start + max_pages, total)
            dst = fitz.open()
            dst.insert_pdf(src, from_page=start, to_page=end - 1)
            parts.append(dst.tobytes())
            dst.close()
        src.close()
        return parts

    async def _extract_pdf(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        import structlog
        logger = structlog.get_logger()

        pages = await self._call_mistral_ocr(file_bytes)

        # Split parser text by page for combining
        page_texts = parsed_doc.text.split("\n\n") if parsed_doc.text else []

        enriched_parts = []
        for page in pages:
            page_idx = page.get("index", 0)
            page_num = page_idx + 1  # Mistral uses 0-indexed, our system uses 1-indexed
            ocr_markdown = page.get("markdown", "").strip()
            parser_text = page_texts[page_idx].strip() if page_idx < len(page_texts) else ""

            # Mistral OCR markdown already includes all text + tables/charts,
            # so when OCR succeeds, use it as sole source to avoid duplication
            # in the chunker (which concatenates text + vision_text).
            if ocr_markdown:
                parsed_doc.pages.append({
                    "index": page_num,
                    "text": "",
                    "vision_text": ocr_markdown,
                })
                enriched_parts.append(ocr_markdown)
            else:
                parsed_doc.pages.append({
                    "index": page_num,
                    "text": parser_text,
                    "vision_text": "",
                })
                if parser_text:
                    enriched_parts.append(parser_text)

        logger.info(
            "mistral_ocr.pdf_extraction_done",
            filename=filename,
            total_pages=len(pages),
        )
        return "\n\n".join(enriched_parts)

    async def _extract_pptx(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        """Convert PPTX to PDF via LibreOffice, then send to Mistral OCR."""
        import asyncio
        import structlog
        logger = structlog.get_logger()

        pdf_bytes = await asyncio.to_thread(self._pptx_to_pdf_bytes, file_bytes)
        pages = await self._call_mistral_ocr(pdf_bytes)

        # Replace slide content with OCR markdown when available
        # (Mistral OCR already captures all text + tables/charts, so parser text is redundant)
        for page in pages:
            page_idx = page.get("index", 0)
            ocr_markdown = page.get("markdown", "").strip()
            if ocr_markdown and page_idx < len(parsed_doc.slides):
                parsed_doc.slides[page_idx]["content"] = [ocr_markdown]

        # Rebuild full text
        text_parts = []
        for slide in parsed_doc.slides:
            slide_text = f"--- Slide {slide['index']} ---\n"
            if slide.get("title"):
                slide_text += f"Title: {slide['title']}\n"
            for content in slide.get("content", []):
                slide_text += f"{content}\n"
            if slide.get("notes"):
                slide_text += f"Speaker Notes: {slide['notes']}\n"
            text_parts.append(slide_text)

        logger.info(
            "mistral_ocr.pptx_extraction_done",
            filename=filename,
            total_slides=len(parsed_doc.slides),
            ocr_pages=len(pages),
        )
        return "\n\n".join(text_parts)

    @staticmethod
    def _pptx_to_pdf_bytes(file_bytes: bytes) -> bytes:
        """Convert PPTX to PDF bytes via LibreOffice (sync, run in thread)."""
        import tempfile
        from pathlib import Path

        from src.processing.ocr.slide_renderer import _libreoffice_convert

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            pptx_path = tmp_path / "input.pptx"
            pptx_path.write_bytes(file_bytes)
            pdf_path = _libreoffice_convert(pptx_path, tmp_path)
            return pdf_path.read_bytes()


class HybridExtractor(BaseOCRExtractor):
    """Hybrid: Mistral OCR for text pages, Vision LLM for chart-heavy pages.

    For PPTX: checks each slide's ``charts`` and ``images`` lists from the parser.
    Slides with charts/images → rendered as PNG → GPT-4.1-mini vision.
    Text-only slides → Mistral OCR markdown (cheaper, faster).

    For PDF: Mistral OCR for all pages, then vision LLM only on pages where
    the OCR returned very little text (likely chart/image-heavy pages).
    """

    async def extract(
        self,
        file_bytes: bytes,
        filename: str,
        file_type: str,
        parsed_doc: ParsedDocument,
    ) -> str:
        if file_type == "pptx":
            return await self._extract_pptx(file_bytes, filename, parsed_doc)
        elif file_type == "pdf":
            return await self._extract_pdf(file_bytes, filename, parsed_doc)
        return parsed_doc.text

    async def _extract_pptx(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        """PPTX hybrid: Mistral for text slides, vision LLM for chart slides."""
        import asyncio

        import structlog

        from src.processing.ocr.slide_renderer import render_slides
        from src.processing.ocr.vision_extractor import VisionSlideExtractor

        logger = structlog.get_logger()

        # Classify slides: which need vision LLM?
        vision_indices: set[int] = set()  # 0-based
        for i, slide in enumerate(parsed_doc.slides):
            has_charts = bool(slide.get("charts"))
            has_images = bool(slide.get("images"))
            if has_charts or has_images:
                vision_indices.add(i)

        logger.info(
            "hybrid.pptx_classification",
            filename=filename,
            total_slides=len(parsed_doc.slides),
            vision_slides=len(vision_indices),
            text_slides=len(parsed_doc.slides) - len(vision_indices),
        )

        # Step 1: Get Mistral OCR for ALL slides (cheap baseline)
        mistral = MistralOCRExtractor()
        pdf_bytes = await asyncio.to_thread(mistral._pptx_to_pdf_bytes, file_bytes)
        mistral_pages = await mistral._call_mistral_ocr(pdf_bytes)

        # Build {0-based index: markdown} from Mistral
        mistral_by_idx: dict[int, str] = {}
        for page in mistral_pages:
            mistral_by_idx[page.get("index", 0)] = page.get("markdown", "").strip()

        # Step 2: Render + vision LLM only for chart/image slides
        vision_results: dict[int, str] = {}  # 1-based index
        if vision_indices:
            slide_images = await render_slides(file_bytes, filename)
            vision_slides = []
            for i in vision_indices:
                if i < len(slide_images):
                    slide_data = parsed_doc.slides[i] if i < len(parsed_doc.slides) else {}
                    parser_text = "\n".join(slide_data.get("content", []))
                    vision_slides.append({
                        "index": i + 1,
                        "image": slide_images[i],
                        "text": parser_text,
                    })

            if vision_slides:
                extractor = VisionSlideExtractor()
                vision_results = await extractor.extract_batch(vision_slides)

        logger.info(
            "hybrid.vision_done",
            filename=filename,
            vision_extracted=len(vision_results),
        )

        # Step 3: Merge — vision LLM for chart slides, Mistral for text slides
        for i, slide in enumerate(parsed_doc.slides):
            page_num = i + 1
            vision_text = vision_results.get(page_num, "")
            if vision_text and vision_text.strip().upper() != "NONE":
                # Vision slide: use parser text + vision extraction (like vision_llm does)
                slide["content"].append(vision_text)
            else:
                # Text slide: replace with Mistral OCR markdown (no duplication)
                mistral_md = mistral_by_idx.get(i, "")
                if mistral_md:
                    slide["content"] = [mistral_md]

        # Rebuild full text
        text_parts = []
        for slide in parsed_doc.slides:
            slide_text = f"--- Slide {slide['index']} ---\n"
            if slide.get("title"):
                slide_text += f"Title: {slide['title']}\n"
            for content in slide.get("content", []):
                slide_text += f"{content}\n"
            if slide.get("notes"):
                slide_text += f"Speaker Notes: {slide['notes']}\n"
            text_parts.append(slide_text)

        logger.info(
            "hybrid.pptx_done",
            filename=filename,
            total_slides=len(parsed_doc.slides),
            vision_slides=len(vision_indices),
        )
        return "\n\n".join(text_parts)

    async def _extract_pdf(
        self, file_bytes: bytes, filename: str, parsed_doc: ParsedDocument
    ) -> str:
        """PDF: delegate to full vision LLM (best quality for complex tables/charts)."""
        vision = VisionLLMExtractor()
        return await vision._extract_pdf(file_bytes, filename, parsed_doc)


# Registry of available extraction methods
OCR_EXTRACTORS: dict[str, type[BaseOCRExtractor]] = {
    "vision_llm": VisionLLMExtractor,
    "mistral_ocr": MistralOCRExtractor,
    "hybrid": HybridExtractor,
}


def get_ocr_extractor(extraction_method: str) -> BaseOCRExtractor:
    """Factory to get the right OCR extractor by method name."""
    cls = OCR_EXTRACTORS.get(extraction_method)
    if cls is None:
        raise ValueError(
            f"Unknown extraction method: {extraction_method!r}. "
            f"Available: {list(OCR_EXTRACTORS.keys())}"
        )
    return cls()
