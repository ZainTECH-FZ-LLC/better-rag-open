"""Vision-based page/slide content extraction using GPT-4.1-mini.

Sends each rendered PNG image (slide or PDF page) to a vision model that
understands spatial layout — axes, legends, stacked segments — and returns
structured data (markdown tables with exact numbers) instead of flat OCR text.

Flows:
  PPTX → LibreOffice PDF → pymupdf PNG → vision model → markdown
  PDF  → pymupdf PNG → vision model → markdown
"""

from __future__ import annotations

import asyncio
import base64

import structlog
from openai import AsyncAzureOpenAI

from config.settings import get_settings

logger = structlog.get_logger()

# Prompt for pages/slides where we have parser-extracted text context
EXTRACTION_WITH_CONTEXT_PROMPT = """\
You are a precise data-extraction assistant. You will receive an image of a \
document page (PDF page or PowerPoint slide).

Text already extracted from this page (may be incomplete):
---
{page_text}
---

Your task:
1. Describe the page's visual content comprehensively — charts, graphs, \
tables, diagrams, images, infographics, and any visual data.
2. For each chart/graph/table, output:
   - Chart title and type (bar, line, pie, stacked bar, waterfall, etc.)
   - Axis labels and units
   - Legend entries
   - ALL data points as a markdown table with exact numbers as shown.
3. Include annotations, growth percentages, callouts, arrows, and footnotes.
4. If the extracted text above is missing content visible in the image \
(e.g., text in shapes, SmartArt, grouped objects, watermarks), include it.

Rules:
- Use EXACT numbers as displayed — do not round or estimate.
- Use the same decimal separator shown in the image (comma or period).
- If a number is partially obscured, note it with [unclear].
- Do NOT repeat text already provided above.
- If there is NO visual content beyond the extracted text (no charts, graphs, \
tables, diagrams, or images), respond with exactly: NONE
- Output ONLY the extracted content, no meta-commentary.
"""

# Prompt for pages/slides where we have no parser text
EXTRACTION_NO_CONTEXT_PROMPT = """\
You are a precise data-extraction assistant. You will receive an image of a \
document page (PDF page or PowerPoint slide).

Your task:
1. Extract ALL text and visual content from the page.
2. For each chart/graph/table, output:
   - Chart title and type (bar, line, pie, stacked bar, waterfall, etc.)
   - Axis labels and units
   - Legend entries
   - ALL data points as a markdown table with exact numbers as shown.
3. Include headings, bullet points, annotations, growth percentages, \
callouts, arrows, footnotes, and any other visible text.

Rules:
- Use EXACT numbers as displayed — do not round or estimate.
- Use the same decimal separator shown in the image (comma or period).
- If a number is partially obscured, note it with [unclear].
- Output ONLY the extracted content, no meta-commentary.
"""

# Concurrency limit to avoid rate-limiting on the vision model
MAX_CONCURRENT = 10


class VisionSlideExtractor:
    """Extracts structured data from page/slide images using a vision-capable LLM."""

    _client: AsyncAzureOpenAI | None = None

    def __init__(self) -> None:
        self.settings = get_settings()

    def _get_client(self) -> AsyncAzureOpenAI:
        if VisionSlideExtractor._client is None:
            VisionSlideExtractor._client = AsyncAzureOpenAI(
                azure_endpoint=self.settings.VISION_AZURE_ENDPOINT,
                api_key=self.settings.VISION_AZURE_API_KEY,
                api_version=self.settings.VISION_AZURE_API_VERSION,
            )
        return VisionSlideExtractor._client

    async def extract_slide(
        self,
        image_png: bytes,
        slide_text: str = "",
    ) -> str:
        """Send a single slide PNG to the vision model.

        Args:
            image_png: Full-slide PNG image bytes (from slide_renderer).
            slide_text: Text already extracted by python-pptx for context.

        Returns:
            Structured markdown description of the visual content.
        """
        client = self._get_client()

        if slide_text.strip():
            system_prompt = EXTRACTION_WITH_CONTEXT_PROMPT.format(
                page_text=slide_text.strip()
            )
        else:
            system_prompt = EXTRACTION_NO_CONTEXT_PROMPT

        b64 = base64.b64encode(image_png).decode("utf-8")
        content: list[dict] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": "Extract all visual content from this page image.",
            },
        ]

        try:
            response = await client.chat.completions.create(
                model=self.settings.VISION_AZURE_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                max_tokens=4096,
                temperature=0,
            )
            result = response.choices[0].message.content or ""
            logger.info(
                "vision_extractor.slide_done",
                result_length=len(result),
            )
            return result.strip()
        except Exception as e:
            logger.warn(
                "vision_extractor.failed",
                error=str(e) or repr(e),
                error_type=type(e).__name__,
            )
            return ""

    async def extract_batch(
        self,
        slides: list[dict],
    ) -> dict[int, str]:
        """Process multiple slides concurrently with rate limiting.

        Args:
            slides: List of dicts with keys:
                - index: slide number (1-based)
                - image: PNG bytes of the rendered slide
                - text: text already extracted by parser

        Returns:
            {slide_index: extracted_markdown} for all slides.
        """
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def _process_one(slide: dict) -> tuple[int, str]:
            idx = slide["index"]
            image = slide.get("image", b"")
            text = slide.get("text", "")
            if not image:
                return idx, ""
            async with semaphore:
                result = await self.extract_slide(image, slide_text=text)
            return idx, result

        results = await asyncio.gather(*(_process_one(s) for s in slides))
        return {idx: text for idx, text in results if text}
