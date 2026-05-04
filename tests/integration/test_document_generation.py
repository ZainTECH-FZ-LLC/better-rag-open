"""Integration tests for the document generation pipeline."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

pytestmark = pytest.mark.integration


class TestPptxGenerator:
    """Test PPTX generation end-to-end."""

    @pytest.mark.asyncio
    async def test_generates_valid_pptx(self, tmp_path):
        from src.document_generation.pptx_generator import PptxGenerator
        from src.document_generation.base import DocumentSpec

        spec = DocumentSpec(
            doc_type="pptx",
            title="Q3 2024 Review",
            sections=[
                {
                    "title": "Executive Summary",
                    "content_type": "bullets",
                    "content": "Revenue: $14.1M\nGrowth: 22%\nHeadcount: 245",
                },
                {
                    "title": "Revenue by Region",
                    "content_type": "table",
                    "content": {
                        "headers": ["Region", "Revenue", "vs Target"],
                        "rows": [
                            ["Americas", "$6.1M", "+11%"],
                            ["EMEA", "$5.2M", "+4%"],
                        ],
                    },
                },
            ],
            style="finance",
        )

        gen = PptxGenerator()
        result = await gen.generate(spec, tmp_path)

        assert result.filepath.exists()
        assert result.filepath.suffix == ".pptx"
        assert result.file_type == "pptx"
        assert result.size_bytes > 0

        # Validate it's a valid PPTX (ZIP with correct structure)
        import zipfile
        assert zipfile.is_zipfile(result.filepath)
        with zipfile.ZipFile(result.filepath) as z:
            names = z.namelist()
            assert "[Content_Types].xml" in names
            assert any("slide" in n for n in names)

    @pytest.mark.asyncio
    async def test_generates_valid_docx(self, tmp_path):
        from src.document_generation.docx_generator import DocxGenerator
        from src.document_generation.base import DocumentSpec

        spec = DocumentSpec(
            doc_type="docx",
            title="HR Policy Manual",
            sections=[
                {"heading": "Leave Policy", "content": "20 days PTO per year.", "level": 1},
                {"heading": "Sick Leave", "content": "10 days per year.", "level": 2},
            ],
            style="hr",
        )

        gen = DocxGenerator()
        result = await gen.generate(spec, tmp_path)

        assert result.filepath.exists()
        assert result.filepath.suffix == ".docx"
        assert result.file_type == "docx"
        assert result.size_bytes > 0

        import zipfile
        assert zipfile.is_zipfile(result.filepath)
        with zipfile.ZipFile(result.filepath) as z:
            names = z.namelist()
            assert "word/document.xml" in names

    @pytest.mark.asyncio
    async def test_generates_valid_xlsx(self, tmp_path):
        from src.document_generation.xlsx_generator import XlsxGenerator
        from src.document_generation.base import DocumentSpec

        spec = DocumentSpec(
            doc_type="xlsx",
            title="Q3 Pipeline",
            sections=[
                {
                    "name": "Pipeline",
                    "headers": ["Deal", "Value", "Stage"],
                    "rows": [
                        ["Acme Corp", 250000, "Negotiation"],
                        ["Globex Inc", 180000, "Proposal"],
                    ],
                }
            ],
            style="sales",
        )

        gen = XlsxGenerator()
        result = await gen.generate(spec, tmp_path)

        assert result.filepath.exists()
        assert result.filepath.suffix == ".xlsx"
        assert result.file_type == "xlsx"
        assert result.size_bytes > 0

        import zipfile
        assert zipfile.is_zipfile(result.filepath)


class TestStyleLoader:
    """Test that department style JSON files load correctly."""

    @pytest.mark.parametrize("department", ["finance", "sales", "marketing", "hr"])
    def test_style_json_loads(self, department):
        import json
        style_path = (
            Path(__file__).parent.parent.parent
            / "src" / "document_generation" / "templates"
            / department / "_style.json"
        )
        assert style_path.exists(), f"_style.json not found for {department}"
        with open(style_path) as f:
            style = json.load(f)

        assert style["department"] == department
        assert "colors" in style
        assert "typography" in style
        assert "chart_palette" in style
        assert len(style["chart_palette"]) >= 4
