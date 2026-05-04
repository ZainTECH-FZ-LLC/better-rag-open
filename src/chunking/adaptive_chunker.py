"""Adaptive semantic chunker — routes to file-type-specific strategies."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
import tiktoken

from config.settings import get_settings

logger = structlog.get_logger()


@dataclass
class Chunk:
    """A single chunk of document content."""

    content: str
    content_with_context: str  # summary prefix + section heading + content
    chunk_type: str = "text"  # text, table, image_description, summary
    sequence_number: int = 0
    page_numbers: list[int] | None = None
    section_heading: str | None = None
    token_count: int = 0


class AdaptiveSemanticChunker:
    """
    Main chunker orchestrator — routes to file-type-specific strategies.

    Principles:
    - Never split mid-sentence or mid-table-row
    - Never orphan a heading from its content
    - Tables and charts are standalone chunks
    - Target: 400-512 tokens, overlap: 50-75 tokens
    - Every chunk gets a context prefix (doc summary + section heading)
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._encoding = tiktoken.encoding_for_model("gpt-4o")

    def chunk(
        self,
        text: str,
        file_type: str,
        metadata: dict | None = None,
        parsed_doc=None,
    ) -> list[Chunk]:
        """
        Chunk a document using file-type-aware strategy.

        Args:
            text: Full document text.
            file_type: pdf, docx, pptx, xlsx.
            metadata: Dict with document_id, document_title, sharepoint_url,
                     department, summary_prefix.
            parsed_doc: ParsedDocument with sections, slides, sheets, etc.
        """
        metadata = metadata or {}
        summary_prefix = metadata.get("summary_prefix", "")
        section_summaries: dict[str, str] = metadata.get("section_summaries", {})

        # Route to file-type strategy
        if file_type == "pptx" and parsed_doc and parsed_doc.slides:
            raw_chunks = self._chunk_pptx(parsed_doc.slides)
        elif file_type in ("xlsx", "xlsb") and parsed_doc and parsed_doc.sheets:
            raw_chunks = self._chunk_xlsx(parsed_doc.sheets)
        elif file_type == "docx" and parsed_doc and parsed_doc.sections:
            raw_chunks = self._chunk_with_sections(text, parsed_doc.sections)
        elif file_type == "pdf" and parsed_doc and parsed_doc.pages:
            raw_chunks = self._chunk_pdf_pages(parsed_doc.pages)
        else:
            raw_chunks = self._chunk_text(text)

        # Add context prefix and compute token counts
        chunks = []
        for i, raw in enumerate(raw_chunks):
            # Build context prefix
            prefix_parts = []
            if summary_prefix:
                prefix_parts.append(f"Document Summary: {summary_prefix[:300]}")
            # Inject per-section summary (sheet summaries for XLSX)
            if section_summaries and raw.section_heading:
                sec_summary = section_summaries.get(raw.section_heading)
                if sec_summary:
                    prefix_parts.append(f"Sheet Summary: {sec_summary}")
            if raw.section_heading:
                prefix_parts.append(f"Section: {raw.section_heading}")

            prefix = "\n".join(prefix_parts)
            raw.content_with_context = (
                f"{prefix}\n\n{raw.content}" if prefix else raw.content
            )
            raw.sequence_number = i
            raw.token_count = len(self._encoding.encode(raw.content_with_context))
            chunks.append(raw)

        # Add summary as a special chunk
        if summary_prefix:
            summary_chunk = Chunk(
                content=summary_prefix,
                content_with_context=f"Document Summary: {summary_prefix}",
                chunk_type="summary",
                sequence_number=-1,
                token_count=len(self._encoding.encode(summary_prefix)),
            )
            chunks.insert(0, summary_chunk)

        logger.info(
            "chunker.completed",
            file_type=file_type,
            chunk_count=len(chunks),
        )
        return chunks

    def _chunk_pdf_pages(self, pages: list[dict]) -> list[Chunk]:
        """
        PDF vision strategy: one chunk per page.

        Keeps parser text and vision-extracted content (charts, tables, diagrams)
        together on the same page — tables are never split mid-row and chart data
        stays next to its surrounding narrative.
        """
        chunks = []
        for page in pages:
            page_num = page.get("index", 0)
            parser_text = page.get("text", "").strip()
            vision_text = page.get("vision_text", "").strip()

            parts = []
            if parser_text:
                parts.append(parser_text)
            if vision_text:
                parts.append(vision_text)

            content = "\n\n".join(parts)
            if not content:
                continue

            # Determine chunk type: if the page is mostly vision-extracted visual
            # content (no parser text), mark it as image_description.
            if vision_text and not parser_text:
                chunk_type = "image_description"
            else:
                chunk_type = "text"

            chunks.append(Chunk(
                content=content,
                content_with_context="",
                chunk_type=chunk_type,
                page_numbers=[page_num],
            ))

        return chunks

    def _chunk_text(self, text: str) -> list[Chunk]:
        """Default text chunking — split at paragraph boundaries."""
        target_tokens = self.settings.CHUNK_TARGET_TOKENS
        max_tokens = self.settings.CHUNK_MAX_TOKENS
        overlap_tokens = self.settings.CHUNK_OVERLAP_TOKENS

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        chunks = []
        current_parts: list[str] = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = len(self._encoding.encode(para))

            # Table detection — keep as standalone chunk
            if para.startswith("|") and "|" in para:
                if current_parts:
                    chunks.append(Chunk(
                        content="\n\n".join(current_parts),
                        content_with_context="",
                        chunk_type="text",
                    ))
                    current_parts = []
                    current_tokens = 0

                chunks.append(Chunk(
                    content=para,
                    content_with_context="",
                    chunk_type="table",
                ))
                continue

            # If adding this paragraph exceeds max, flush current chunk
            if current_tokens + para_tokens > max_tokens and current_parts:
                chunks.append(Chunk(
                    content="\n\n".join(current_parts),
                    content_with_context="",
                    chunk_type="text",
                ))

                # Overlap: keep last paragraph(s) within overlap budget
                overlap_parts = []
                overlap_count = 0
                for p in reversed(current_parts):
                    p_tokens = len(self._encoding.encode(p))
                    if overlap_count + p_tokens <= overlap_tokens:
                        overlap_parts.insert(0, p)
                        overlap_count += p_tokens
                    else:
                        break

                current_parts = overlap_parts
                current_tokens = overlap_count

            current_parts.append(para)
            current_tokens += para_tokens

        # Flush remaining
        if current_parts:
            chunks.append(Chunk(
                content="\n\n".join(current_parts),
                content_with_context="",
                chunk_type="text",
            ))

        return chunks

    def _chunk_with_sections(self, text: str, sections: list[dict]) -> list[Chunk]:
        """Section-based chunking for DOCX — split at heading boundaries."""
        if not sections:
            return self._chunk_text(text)

        max_tokens = self.settings.CHUNK_MAX_TOKENS
        chunks = []

        # Split text into section segments
        lines = text.split("\n")
        section_segments = []
        current_heading = None
        current_lines: list[str] = []

        for line in lines:
            # Check if this line is a section heading
            matched_section = None
            for section in sections:
                if section["heading"] in line and line.strip():
                    matched_section = section
                    break

            if matched_section:
                if current_lines:
                    section_segments.append({
                        "heading": current_heading,
                        "content": "\n".join(current_lines).strip(),
                    })
                current_heading = matched_section["heading"]
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            section_segments.append({
                "heading": current_heading,
                "content": "\n".join(current_lines).strip(),
            })

        # Process each section
        for segment in section_segments:
            content = segment["content"]
            heading = segment["heading"]
            content_tokens = len(self._encoding.encode(content))

            if content_tokens <= max_tokens:
                chunks.append(Chunk(
                    content=content,
                    content_with_context="",
                    section_heading=heading,
                ))
            else:
                # Split large section at paragraph boundaries
                sub_chunks = self._chunk_text(content)
                for sub in sub_chunks:
                    sub.section_heading = heading
                    chunks.append(sub)

        return chunks

    def _chunk_pptx(self, slides: list[dict]) -> list[Chunk]:
        """
        PPTX strategy: each slide = exactly 1 chunk, no token-limit splitting.

        Charts are rendered inline with the slide content so they retain their
        narrative context. Speaker notes are always appended to the same chunk.
        """
        chunks = []

        for slide in slides:
            parts = []
            title = slide.get("title", "")
            if title:
                parts.append(f"# {title}")

            for content in slide.get("content", []):
                if content != title:
                    parts.append(content)

            # Inline chart data — keeps chart values next to slide narrative
            for chart in slide.get("charts", []):
                chart_text = self._render_chart_inline(chart)
                if chart_text:
                    parts.append(chart_text)

            notes = slide.get("notes", "")
            if notes and notes.strip():
                parts.append(f"\nSpeaker Notes: {notes.strip()}")

            text = "\n\n".join(parts)
            if text.strip():
                chunks.append(Chunk(
                    content=text,
                    content_with_context="",
                    section_heading=title or None,
                    page_numbers=[slide.get("index", 0)],
                ))

        return chunks

    def _render_chart_inline(self, chart: dict) -> str:
        """Render chart data as inline text within a slide chunk."""
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

    def _chunk_xlsx(self, sheets: list[dict]) -> list[Chunk]:
        """XLSX strategy: each sheet/data region = 1 chunk, headers repeated."""
        chunks = []

        for sheet in sheets:
            name = sheet.get("name", "Sheet")
            rows = sheet.get("rows", [])
            headers = sheet.get("headers", [])

            if not rows:
                continue

            # If sheet is small enough, keep as single chunk
            text = f"Sheet: {name}\n"
            if headers:
                text += " | ".join(headers) + "\n"
                text += "-" * 40 + "\n"

            for row in rows[1:]:  # Skip header row
                text += " | ".join(row) + "\n"

            tokens = len(self._encoding.encode(text))

            if tokens <= self.settings.CHUNK_MAX_TOKENS:
                chunks.append(Chunk(
                    content=text,
                    content_with_context="",
                    chunk_type="table",
                    section_heading=name,
                ))
            else:
                # Split into row groups, repeating headers
                row_batch: list[str] = []
                batch_tokens = 0
                header_text = " | ".join(headers) + "\n" if headers else ""
                header_tokens = len(self._encoding.encode(header_text))

                for row in rows[1:]:
                    row_text = " | ".join(row) + "\n"
                    row_tokens = len(self._encoding.encode(row_text))

                    if batch_tokens + row_tokens + header_tokens > self.settings.CHUNK_MAX_TOKENS:
                        chunk_text = f"Sheet: {name}\n{header_text}" + "".join(row_batch)
                        chunks.append(Chunk(
                            content=chunk_text,
                            content_with_context="",
                            chunk_type="table",
                            section_heading=name,
                        ))
                        row_batch = []
                        batch_tokens = 0

                    row_batch.append(row_text)
                    batch_tokens += row_tokens

                if row_batch:
                    chunk_text = f"Sheet: {name}\n{header_text}" + "".join(row_batch)
                    chunks.append(Chunk(
                        content=chunk_text,
                        content_with_context="",
                        chunk_type="table",
                        section_heading=name,
                    ))

        return chunks
