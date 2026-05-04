"""PPTX chunking strategy — one chunk per slide, no token-limit splitting."""

from __future__ import annotations

import structlog
import tiktoken

from src.chunking.adaptive_chunker import Chunk

logger = structlog.get_logger()

_MIN_SLIDE_TOKENS = 20  # slides smaller than this are merged with the next


class PPTXChunkingStrategy:
    """
    PPTX chunking strategy.

    Primary unit: slide — each slide produces exactly one chunk.

    Design decisions:
    - No token-limit splitting. A slide is a coherent semantic unit; splitting
      it at an arbitrary token boundary creates retrieval artefacts and detaches
      speaker notes from the content they annotate.
    - Charts are rendered inline with the slide text so chart values stay next
      to the narrative context (title, bullet points, speaker notes) rather than
      floating in an isolated chunk.
    - Speaker notes are always appended to the same chunk as the slide body.
    - Very small slides (< 20 tokens) are merged with the following slide to
      avoid near-empty chunks that dilute embedding quality.
    """

    def __init__(self) -> None:
        self._enc = tiktoken.encoding_for_model("gpt-4o")

    def chunk(self, parsed_doc) -> list[Chunk]:
        if not parsed_doc or not getattr(parsed_doc, "slides", None):
            return []
        return self._chunk_slides(parsed_doc.slides)

    def _chunk_slides(self, slides: list[dict]) -> list[Chunk]:
        chunks: list[Chunk] = []
        pending_merge: dict | None = None

        for slide in slides:
            slide_text = self._render_slide(slide)
            slide_tokens = len(self._enc.encode(slide_text))

            if pending_merge is not None:
                # Always merge: the previous slide was too small on its own
                merged_text = (
                    self._render_slide(pending_merge) + "\n\n---\n\n" + slide_text
                )
                chunks.append(Chunk(
                    content=merged_text,
                    content_with_context="",
                    chunk_type="text",
                    section_heading=pending_merge.get("title"),
                    page_numbers=[
                        pending_merge.get("index", 0),
                        slide.get("index", 0),
                    ],
                ))
                pending_merge = None
                continue

            if slide_tokens < _MIN_SLIDE_TOKENS:
                pending_merge = slide
                continue

            chunks.append(Chunk(
                content=slide_text,
                content_with_context="",
                chunk_type="text",
                section_heading=slide.get("title") or None,
                page_numbers=[slide.get("index", 0)],
            ))

        # Flush any trailing tiny slide
        if pending_merge is not None:
            chunks.append(Chunk(
                content=self._render_slide(pending_merge),
                content_with_context="",
                chunk_type="text",
                section_heading=pending_merge.get("title") or None,
                page_numbers=[pending_merge.get("index", 0)],
            ))

        return chunks

    def _render_slide(self, slide: dict) -> str:
        """Render a slide dict to text with charts inlined."""
        parts: list[str] = []

        title = slide.get("title", "")
        if title:
            parts.append(f"# {title}")

        for item in slide.get("content", []):
            if item and item != title:
                parts.append(item)

        # Inline chart data — preserves context between chart values and slide narrative
        for chart in slide.get("charts", []):
            chart_text = self._render_chart_inline(chart)
            if chart_text:
                parts.append(chart_text)

        notes = slide.get("notes", "")
        if notes and notes.strip():
            parts.append(f"\nSpeaker Notes: {notes.strip()}")

        return "\n\n".join(parts)

    def _render_chart_inline(self, chart: dict) -> str:
        """Render chart data as inline text within the slide."""
        chart_type = chart.get("type", "chart")
        title = chart.get("title", "")
        series = chart.get("series", [])
        categories = chart.get("categories", [])

        parts = [f"[Chart: {chart_type}]"]
        if title:
            parts.append(f"Title: {title}")
        if categories:
            parts.append(f"Categories: {', '.join(str(c) for c in categories[:20])}")
        for s in series[:5]:
            s_name = s.get("name", "")
            s_values = s.get("values", [])
            parts.append(f"  {s_name}: {', '.join(str(v) for v in s_values[:10])}")

        result = "\n".join(parts)
        return result if len(result) > 10 else ""
