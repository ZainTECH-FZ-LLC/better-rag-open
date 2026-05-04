"""DOCX chunking strategy — heading-hierarchy section tree."""

from __future__ import annotations

import structlog
import tiktoken

from config.settings import get_settings
from src.chunking.adaptive_chunker import Chunk

logger = structlog.get_logger()


class DOCXChunkingStrategy:
    """
    DOCX chunking strategy.

    Primary unit: leaf section in the heading hierarchy.
    - Respects heading levels (H1 > H2 > H3 > paragraph)
    - Leaf sections are the primary chunks
    - Small sibling sections merged when under target token count
    - Large sections split at paragraph boundaries with overlap
    - Tables detected inline → standalone table chunks
    - Footnotes appended to the section they belong to
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._enc = tiktoken.encoding_for_model("gpt-4o")

    def chunk(self, parsed_doc) -> list[Chunk]:
        """
        Chunk a DOCX document.

        Args:
            parsed_doc: ParsedDocument with .sections list. Each section dict:
                        heading, level (1-6), content (str), subsections (list[dict]),
                        page_numbers (list[int])

        Returns:
            List of Chunk objects.
        """
        if not parsed_doc or not getattr(parsed_doc, "sections", None):
            return []

        return self._process_sections(parsed_doc.sections, parent_heading=None)

    def _process_sections(
        self,
        sections: list[dict],
        parent_heading: str | None,
    ) -> list[Chunk]:
        """Recursively process a list of sections into chunks."""
        chunks: list[Chunk] = []
        target = self.settings.CHUNK_TARGET_TOKENS
        max_tokens = self.settings.CHUNK_MAX_TOKENS

        i = 0
        while i < len(sections):
            section = sections[i]
            heading = section.get("heading") or parent_heading
            content = section.get("content", "").strip()
            subsections = section.get("subsections", [])
            page_numbers = section.get("page_numbers", [])

            # If section has subsections, recurse into them
            if subsections:
                # Include the section's own content before recursing
                if content:
                    table_chunks, remaining = self._extract_tables(
                        content, heading, page_numbers
                    )
                    chunks.extend(table_chunks)
                    if remaining.strip():
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
                            chunks.extend(
                                self._split_paragraphs(remaining, heading, page_numbers)
                            )

                chunks.extend(self._process_sections(subsections, heading))
                i += 1
                continue

            # Leaf section — try to merge with small siblings
            section_tokens = len(self._enc.encode(content)) if content else 0

            if section_tokens < target // 2 and i + 1 < len(sections):
                # Attempt merge with next sibling if it's also a leaf
                next_section = sections[i + 1]
                if not next_section.get("subsections"):
                    next_content = next_section.get("content", "").strip()
                    next_heading = next_section.get("heading") or heading
                    next_page = next_section.get("page_numbers", [])
                    merged = f"## {heading}\n\n{content}\n\n## {next_heading}\n\n{next_content}"
                    merged_tokens = len(self._enc.encode(merged))

                    if merged_tokens <= max_tokens:
                        table_chunks, remaining = self._extract_tables(
                            merged, heading, page_numbers + next_page
                        )
                        chunks.extend(table_chunks)
                        if remaining.strip():
                            chunks.append(Chunk(
                                content=remaining,
                                content_with_context="",
                                chunk_type="text",
                                section_heading=heading,
                                page_numbers=page_numbers + next_page,
                            ))
                        i += 2
                        continue

            # Regular leaf section
            if content:
                table_chunks, remaining = self._extract_tables(
                    content, heading, page_numbers
                )
                chunks.extend(table_chunks)

                if remaining.strip():
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
                        chunks.extend(
                            self._split_paragraphs(remaining, heading, page_numbers)
                        )

            i += 1

        return chunks

    def _extract_tables(
        self,
        text: str,
        heading: str | None,
        page_numbers: list[int],
    ) -> tuple[list[Chunk], str]:
        """Extract table blocks as standalone chunks, returning remaining text."""
        table_chunks: list[Chunk] = []
        lines = text.split("\n")
        remaining_lines: list[str] = []
        table_buffer: list[str] = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            is_table = stripped.startswith("|") and stripped.endswith("|")

            if is_table:
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

    def _split_paragraphs(
        self,
        text: str,
        heading: str | None,
        page_numbers: list[int],
    ) -> list[Chunk]:
        """Split large text at paragraph boundaries with overlap."""
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
