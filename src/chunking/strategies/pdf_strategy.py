"""PDF chunking strategy — section/heading-based with tables as standalone chunks."""

from __future__ import annotations

import re

import structlog
import tiktoken

from config.settings import get_settings
from src.chunking.adaptive_chunker import Chunk

logger = structlog.get_logger()

# Azure Document Intelligence paragraph roles that indicate headings
HEADING_ROLES = {"title", "sectionHeading", "heading"}

# Markdown heading pattern (# Heading)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


class PDFChunkingStrategy:
    """
    PDF chunking strategy.

    Primary unit: sections detected by Azure DI heading roles or markdown ## headings.
    - Tables (detected by | delimiters or table markers) → standalone chunks
    - Figures/image descriptions → standalone image_description chunks
    - Large sections split at paragraph boundaries, never mid-sentence
    - Overlap preserved across splits
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._enc = tiktoken.encoding_for_model("gpt-4o")

    def chunk(self, text: str, parsed_doc=None) -> list[Chunk]:
        """
        Chunk a PDF document.

        Args:
            text: Full document text (markdown output from Azure DI or PyMuPDF).
            parsed_doc: Optional ParsedDocument with structured section/heading info.

        Returns:
            List of Chunk objects.
        """
        if parsed_doc and getattr(parsed_doc, "sections", None):
            return self._chunk_from_sections(parsed_doc.sections)
        return self._chunk_from_markdown(text)

    def _chunk_from_sections(self, sections: list[dict]) -> list[Chunk]:
        """Chunk using structured sections from Azure Document Intelligence."""
        chunks: list[Chunk] = []
        max_tokens = self.settings.CHUNK_MAX_TOKENS

        for section in sections:
            heading = section.get("heading", "")
            content = section.get("content", "").strip()
            page_numbers = section.get("page_numbers", [])

            if not content:
                continue

            # Tables in sections → standalone
            table_chunks, remaining = self._extract_tables(content, heading, page_numbers)
            chunks.extend(table_chunks)

            if not remaining.strip():
                continue

            tokens = len(self._enc.encode(remaining))
            if tokens <= max_tokens:
                chunks.append(Chunk(
                    content=remaining,
                    content_with_context="",
                    chunk_type="text",
                    section_heading=heading,
                    page_numbers=page_numbers,
                ))
            else:
                sub_chunks = self._split_at_paragraphs(remaining, heading, page_numbers)
                chunks.extend(sub_chunks)

        return chunks

    def _chunk_from_markdown(self, text: str) -> list[Chunk]:
        """Chunk using markdown heading detection (fallback when no structured sections)."""
        chunks: list[Chunk] = []
        max_tokens = self.settings.CHUNK_MAX_TOKENS

        # Split at markdown headings
        segments = _MD_HEADING_RE.split(text)
        # segments alternates: [pre-heading text, heading_text, section_body, ...]

        if len(segments) <= 1:
            # No headings found — chunk as plain text
            return self._split_at_paragraphs(text, None, [])

        # First segment (before any heading)
        if segments[0].strip():
            chunks.extend(
                self._split_at_paragraphs(segments[0].strip(), None, [])
            )

        # Pair headings with their content
        for i in range(1, len(segments), 2):
            heading = segments[i].strip() if i < len(segments) else ""
            body = segments[i + 1].strip() if (i + 1) < len(segments) else ""

            if not body:
                continue

            table_chunks, remaining = self._extract_tables(body, heading, [])
            chunks.extend(table_chunks)

            if not remaining.strip():
                continue

            tokens = len(self._enc.encode(remaining))
            if tokens <= max_tokens:
                chunks.append(Chunk(
                    content=remaining,
                    content_with_context="",
                    chunk_type="text",
                    section_heading=heading,
                ))
            else:
                chunks.extend(self._split_at_paragraphs(remaining, heading, []))

        return chunks

    def _extract_tables(
        self,
        text: str,
        heading: str | None,
        page_numbers: list[int],
    ) -> tuple[list[Chunk], str]:
        """
        Extract table blocks from text, returning them as standalone chunks.

        Returns:
            (table_chunks, remaining_text_without_tables)
        """
        table_chunks: list[Chunk] = []
        lines = text.split("\n")
        remaining_lines: list[str] = []
        table_buffer: list[str] = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            is_table_line = stripped.startswith("|") and stripped.endswith("|")
            is_separator = bool(re.match(r"^\|[-:| ]+\|$", stripped))

            if is_table_line or is_separator:
                if not in_table and remaining_lines:
                    # Flush any pending non-table content first
                    pass
                in_table = True
                table_buffer.append(line)
            else:
                if in_table and table_buffer:
                    table_text = "\n".join(table_buffer).strip()
                    if table_text:
                        table_chunks.append(Chunk(
                            content=table_text,
                            content_with_context="",
                            chunk_type="table",
                            section_heading=heading,
                            page_numbers=page_numbers,
                        ))
                    table_buffer = []
                    in_table = False
                remaining_lines.append(line)

        # Flush trailing table
        if table_buffer:
            table_text = "\n".join(table_buffer).strip()
            if table_text:
                table_chunks.append(Chunk(
                    content=table_text,
                    content_with_context="",
                    chunk_type="table",
                    section_heading=heading,
                    page_numbers=page_numbers,
                ))

        return table_chunks, "\n".join(remaining_lines)

    def _split_at_paragraphs(
        self,
        text: str,
        heading: str | None,
        page_numbers: list[int],
    ) -> list[Chunk]:
        """Split large text at paragraph boundaries with overlap."""
        target = self.settings.CHUNK_TARGET_TOKENS
        max_tokens = self.settings.CHUNK_MAX_TOKENS
        overlap = self.settings.CHUNK_OVERLAP_TOKENS

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        chunks: list[Chunk] = []
        current_parts: list[str] = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = len(self._enc.encode(para))

            if current_tokens + para_tokens > max_tokens and current_parts:
                chunks.append(Chunk(
                    content="\n\n".join(current_parts),
                    content_with_context="",
                    chunk_type="text",
                    section_heading=heading,
                    page_numbers=page_numbers,
                ))
                # Overlap: keep last paras within overlap budget
                overlap_parts: list[str] = []
                overlap_count = 0
                for p in reversed(current_parts):
                    pt = len(self._enc.encode(p))
                    if overlap_count + pt <= overlap:
                        overlap_parts.insert(0, p)
                        overlap_count += pt
                    else:
                        break
                current_parts = overlap_parts
                current_tokens = overlap_count

            current_parts.append(para)
            current_tokens += para_tokens

        if current_parts:
            chunks.append(Chunk(
                content="\n\n".join(current_parts),
                content_with_context="",
                chunk_type="text",
                section_heading=heading,
                page_numbers=page_numbers,
            ))

        return chunks
