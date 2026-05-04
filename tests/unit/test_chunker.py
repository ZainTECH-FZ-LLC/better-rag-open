"""Unit tests for the adaptive semantic chunker."""

from __future__ import annotations

import pytest

from src.chunking.adaptive_chunker import AdaptiveSemanticChunker


class TestAdaptiveSemanticChunker:
    """Tests for the adaptive chunker's file-type routing and boundary handling."""

    def setup_method(self):
        self.chunker = AdaptiveSemanticChunker()

    def test_basic_text_chunking(self):
        """Simple text is split into chunks."""
        text = "\n\n".join([f"Paragraph {i}. " * 50 for i in range(10)])
        chunks = self.chunker.chunk(text, file_type="pdf")

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content) > 0
            assert chunk.sequence_number >= -1  # -1 for summary chunk

    def test_empty_text(self):
        """Empty text returns empty list (or just summary chunk)."""
        chunks = self.chunker.chunk("", file_type="pdf")
        assert len(chunks) == 0

    def test_short_text_single_chunk(self):
        """Short text stays as a single chunk."""
        text = "This is a short document about Q4 results."
        chunks = self.chunker.chunk(text, file_type="pdf")
        assert len(chunks) == 1
        assert "Q4 results" in chunks[0].content

    def test_table_standalone(self):
        """Tables are extracted as standalone chunks."""
        text = (
            "Some intro text.\n\n"
            "| Col1 | Col2 |\n| A | B |\n| C | D |\n\n"
            "Some following text."
        )
        chunks = self.chunker.chunk(text, file_type="pdf")
        table_chunks = [c for c in chunks if c.chunk_type == "table"]
        assert len(table_chunks) >= 1

    def test_summary_prefix_added(self):
        """Summary prefix is prepended to content_with_context."""
        text = "Document body text."
        chunks = self.chunker.chunk(
            text,
            file_type="pdf",
            metadata={"summary_prefix": "This is a financial report."},
        )

        # First chunk should be the summary itself
        assert chunks[0].chunk_type == "summary"
        assert "financial report" in chunks[0].content

    def test_pptx_slide_per_chunk(self):
        """PPTX: each slide becomes one chunk."""
        from src.processing.parsers.base import ParsedDocument

        parsed = ParsedDocument(
            slides=[
                {"index": 1, "title": "Intro", "content": ["Welcome"], "notes": ""},
                {"index": 2, "title": "Data", "content": ["Numbers here"], "notes": "Note 2"},
            ]
        )

        chunks = self.chunker.chunk("", file_type="pptx", parsed_doc=parsed)
        # Should have 2 slide chunks
        slide_chunks = [c for c in chunks if c.chunk_type != "summary"]
        assert len(slide_chunks) == 2
        assert "Intro" in slide_chunks[0].section_heading
        assert slide_chunks[1].page_numbers == [2]

    def test_xlsx_sheet_per_chunk(self):
        """XLSX: each sheet becomes one or more chunks."""
        from src.processing.parsers.base import ParsedDocument

        parsed = ParsedDocument(
            sheets=[
                {
                    "name": "Revenue",
                    "headers": ["Q1", "Q2", "Q3", "Q4"],
                    "rows": [
                        ["Q1", "Q2", "Q3", "Q4"],
                        ["10M", "12M", "11M", "15M"],
                    ],
                },
            ]
        )

        chunks = self.chunker.chunk("", file_type="xlsx", parsed_doc=parsed)
        table_chunks = [c for c in chunks if c.chunk_type == "table"]
        assert len(table_chunks) >= 1
        assert "Revenue" in table_chunks[0].section_heading

    def test_section_based_chunking(self):
        """DOCX: sections are respected as chunk boundaries."""
        from src.processing.parsers.base import ParsedDocument

        text = (
            "Introduction\nThis is the intro.\n\n"
            "Methods\nWe used method X.\n\n"
            "Results\nThe results showed Y."
        )
        parsed = ParsedDocument(
            sections=[
                {"heading": "Introduction", "level": 1},
                {"heading": "Methods", "level": 1},
                {"heading": "Results", "level": 1},
            ]
        )

        chunks = self.chunker.chunk(text, file_type="docx", parsed_doc=parsed)
        assert len(chunks) >= 3
