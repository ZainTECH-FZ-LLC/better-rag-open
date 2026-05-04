"""XLSX chunking strategy — sheet/data-region-based with column headers repeated."""

from __future__ import annotations

import structlog
import tiktoken

from config.settings import get_settings
from src.chunking.adaptive_chunker import Chunk

logger = structlog.get_logger()


class XLSXChunkingStrategy:
    """
    XLSX chunking strategy.

    Primary unit: contiguous data region within a sheet.
    - Each sheet = 1 or more chunks (split by data region when sheet is large)
    - Column headers repeated in every chunk when a sheet is split
    - Named ranges treated as independent data regions
    - Charts/images serialized as descriptive text chunks
    - Formula values captured (not the formula strings)
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._enc = tiktoken.encoding_for_model("gpt-4o")

    def chunk(self, parsed_doc) -> list[Chunk]:
        """
        Chunk an XLSX document.

        Args:
            parsed_doc: ParsedDocument with .sheets list. Each sheet dict has:
                        name, headers (list[str]), rows (list[list[str]]),
                        named_ranges (list[dict]), charts (list[dict])

        Returns:
            List of Chunk objects.
        """
        if not parsed_doc or not getattr(parsed_doc, "sheets", None):
            return []

        chunks: list[Chunk] = []
        for sheet in parsed_doc.sheets:
            chunks.extend(self._chunk_sheet(sheet))
        return chunks

    def _chunk_sheet(self, sheet: dict) -> list[Chunk]:
        """Process a single sheet into chunks."""
        chunks: list[Chunk] = []
        name = sheet.get("name", "Sheet")
        headers = sheet.get("headers", [])
        rows = sheet.get("rows", [])

        # Skip sheets with no data
        data_rows = [r for r in rows if any(cell.strip() for cell in r if cell)]
        if not data_rows:
            return []

        # Charts as standalone descriptive chunks
        for chart in sheet.get("charts", []):
            chart_chunk = self._render_chart(chart, name)
            if chart_chunk:
                chunks.append(chart_chunk)

        # Named ranges as independent chunks
        for named_range in sheet.get("named_ranges", []):
            nr_chunk = self._render_named_range(named_range, name)
            if nr_chunk:
                chunks.append(nr_chunk)

        # Main table data
        header_text = " | ".join(headers) + "\n" if headers else ""
        header_tokens = len(self._enc.encode(header_text))
        max_tokens = self.settings.CHUNK_MAX_TOKENS

        # Build the full sheet text first
        full_text = f"Sheet: {name}\n"
        if headers:
            full_text += header_text
            full_text += "-" * min(60, len(header_text)) + "\n"
        for row in data_rows:
            full_text += " | ".join(str(cell) for cell in row) + "\n"

        full_tokens = len(self._enc.encode(full_text))

        if full_tokens <= max_tokens:
            chunks.append(Chunk(
                content=full_text,
                content_with_context="",
                chunk_type="table",
                section_heading=name,
            ))
        else:
            # Split into row groups, repeating headers in each chunk
            chunks.extend(
                self._split_rows(name, headers, data_rows, header_tokens)
            )

        return chunks

    def _split_rows(
        self,
        sheet_name: str,
        headers: list[str],
        rows: list[list[str]],
        header_tokens: int,
    ) -> list[Chunk]:
        """Split a large sheet's data rows into multiple chunks, repeating headers."""
        max_tokens = self.settings.CHUNK_MAX_TOKENS
        chunks: list[Chunk] = []
        header_text = " | ".join(headers) + "\n" if headers else ""
        separator = "-" * min(60, len(header_text)) + "\n" if headers else ""

        row_batch: list[str] = []
        batch_tokens = 0
        chunk_num = 1

        for row in rows:
            row_text = " | ".join(str(cell) for cell in row) + "\n"
            row_tokens = len(self._enc.encode(row_text))

            if (
                batch_tokens + row_tokens + header_tokens + 20 > max_tokens
                and row_batch
            ):
                chunk_text = (
                    f"Sheet: {sheet_name} (part {chunk_num})\n"
                    + header_text
                    + separator
                    + "".join(row_batch)
                )
                chunks.append(Chunk(
                    content=chunk_text,
                    content_with_context="",
                    chunk_type="table",
                    section_heading=sheet_name,
                ))
                row_batch = []
                batch_tokens = 0
                chunk_num += 1

            row_batch.append(row_text)
            batch_tokens += row_tokens

        if row_batch:
            suffix = f" (part {chunk_num})" if chunk_num > 1 else ""
            chunk_text = (
                f"Sheet: {sheet_name}{suffix}\n"
                + header_text
                + separator
                + "".join(row_batch)
            )
            chunks.append(Chunk(
                content=chunk_text,
                content_with_context="",
                chunk_type="table",
                section_heading=sheet_name,
            ))

        return chunks

    def _render_chart(self, chart: dict, sheet_name: str) -> Chunk | None:
        """Render a chart as a descriptive text chunk."""
        chart_type = chart.get("type", "chart")
        title = chart.get("title", "")
        series = chart.get("series", [])

        parts = [f"Chart ({chart_type}) on sheet '{sheet_name}'"]
        if title:
            parts.append(f"Title: {title}")
        for s in series[:5]:
            s_name = s.get("name", "")
            s_values = s.get("values", [])
            if s_values:
                parts.append(f"Series '{s_name}': {', '.join(str(v) for v in s_values[:15])}")

        content = "\n".join(parts)
        if len(content) < 15:
            return None

        return Chunk(
            content=content,
            content_with_context="",
            chunk_type="image_description",
            section_heading=sheet_name,
        )

    def _render_named_range(self, named_range: dict, sheet_name: str) -> Chunk | None:
        """Render a named range as a focused data chunk."""
        name = named_range.get("name", "")
        headers = named_range.get("headers", [])
        rows = named_range.get("rows", [])

        if not rows:
            return None

        parts = [f"Named Range: {name} (sheet: {sheet_name})"]
        if headers:
            parts.append(" | ".join(headers))
        for row in rows[:50]:
            parts.append(" | ".join(str(c) for c in row))

        content = "\n".join(parts)
        return Chunk(
            content=content,
            content_with_context="",
            chunk_type="table",
            section_heading=name or sheet_name,
        )
