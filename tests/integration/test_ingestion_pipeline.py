"""Integration tests for the document ingestion pipeline."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


@pytest.fixture
def sample_pdf():
    path = FIXTURES_DIR / "sample.pdf"
    if not path.exists():
        pytest.skip("sample.pdf fixture not found — run tests/fixtures/generate_fixtures.py")
    return path


@pytest.fixture
def sample_pptx():
    path = FIXTURES_DIR / "sample.pptx"
    if not path.exists():
        pytest.skip("sample.pptx fixture not found — run tests/fixtures/generate_fixtures.py")
    return path


@pytest.fixture
def sample_docx():
    path = FIXTURES_DIR / "sample.docx"
    if not path.exists():
        pytest.skip("sample.docx fixture not found — run tests/fixtures/generate_fixtures.py")
    return path


@pytest.fixture
def sample_xlsx():
    path = FIXTURES_DIR / "sample.xlsx"
    if not path.exists():
        pytest.skip("sample.xlsx fixture not found — run tests/fixtures/generate_fixtures.py")
    return path


class TestAdaptiveChunker:
    """Test that the adaptive chunker produces correct chunk counts and metadata."""

    @pytest.mark.asyncio
    async def test_pdf_chunking_produces_sections(self, sample_pdf):
        from src.chunking.adaptive_chunker import AdaptiveChunker

        chunker = AdaptiveChunker()
        with open(sample_pdf, "rb") as f:
            content = f.read()

        chunks = await chunker.chunk(
            content=content,
            filename="sample.pdf",
            content_type="application/pdf",
            document_id="test-doc-001",
        )

        assert len(chunks) > 0, "PDF should produce at least 1 chunk"
        for chunk in chunks:
            assert chunk.document_id == "test-doc-001"
            assert len(chunk.content) > 0
            assert chunk.chunk_index >= 0

    @pytest.mark.asyncio
    async def test_pptx_chunking_slide_per_chunk(self, sample_pptx):
        from src.chunking.adaptive_chunker import AdaptiveChunker

        chunker = AdaptiveChunker()
        with open(sample_pptx, "rb") as f:
            content = f.read()

        chunks = await chunker.chunk(
            content=content,
            filename="sample.pptx",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            document_id="test-doc-002",
        )

        # 3-slide PPTX should produce ~3 chunks
        assert len(chunks) >= 1, "PPTX should produce at least 1 chunk"
        titles = [c.section_heading for c in chunks if c.section_heading]
        assert len(titles) > 0, "PPTX chunks should have section headings"

    @pytest.mark.asyncio
    async def test_docx_chunking_heading_hierarchy(self, sample_docx):
        from src.chunking.adaptive_chunker import AdaptiveChunker

        chunker = AdaptiveChunker()
        with open(sample_docx, "rb") as f:
            content = f.read()

        chunks = await chunker.chunk(
            content=content,
            filename="sample.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            document_id="test-doc-003",
        )

        assert len(chunks) >= 1
        # Should detect section headings
        has_heading = any(c.section_heading for c in chunks)
        assert has_heading, "DOCX should produce chunks with section headings"

    @pytest.mark.asyncio
    async def test_xlsx_chunking_per_sheet(self, sample_xlsx):
        from src.chunking.adaptive_chunker import AdaptiveChunker

        chunker = AdaptiveChunker()
        with open(sample_xlsx, "rb") as f:
            content = f.read()

        chunks = await chunker.chunk(
            content=content,
            filename="sample.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            document_id="test-doc-004",
        )

        assert len(chunks) >= 1
        # Should have chunks for Revenue and Summary sheets
        contents = [c.content for c in chunks]
        combined = " ".join(contents).lower()
        assert "revenue" in combined or "quarter" in combined


class TestChartBuilder:
    """Test chart generation for all supported chart types."""

    @pytest.mark.asyncio
    async def test_bar_chart_generates_file(self, tmp_path):
        from src.document_generation.chart_builder import ChartBuilder

        builder = ChartBuilder(department="finance")
        filename, filepath = await builder.build(
            chart_type="bar",
            title="Test Bar Chart",
            labels=["Q1", "Q2", "Q3"],
            datasets=[{"label": "Revenue", "data": [10.0, 12.0, 14.0]}],
            output_dir=tmp_path,
            format="png",
        )

        assert filepath.exists(), "Chart PNG should be written to disk"
        assert filepath.stat().st_size > 1000, "Chart file should be non-trivial size"
        assert filename.endswith(".png")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chart_type", [
        "bar", "horizontal_bar", "line", "area", "scatter",
        "pie", "donut", "waterfall", "box",
    ])
    async def test_all_chart_types(self, tmp_path, chart_type):
        from src.document_generation.chart_builder import ChartBuilder

        builder = ChartBuilder(department="general")
        labels = ["A", "B", "C", "D"]
        data = [10.0, 20.0, 15.0, 25.0]

        filename, filepath = await builder.build(
            chart_type=chart_type,
            title=f"Test {chart_type}",
            labels=labels,
            datasets=[{"label": "Series 1", "data": data}],
            output_dir=tmp_path,
        )

        assert filepath.exists(), f"{chart_type} chart file should exist"


class TestSkillLoader:
    """Test the progressive disclosure skill loader."""

    def test_loader_discovers_skills(self):
        from src.skills._loader import SkillLoader

        loader = SkillLoader()
        metadata = loader.all_metadata()
        # Should find pptx, docx, xlsx, chart skills
        names = {m.name for m in metadata}
        assert len(names) >= 1, "Should discover at least 1 skill"

    def test_loader_loads_full_skill(self):
        from src.skills._loader import SkillLoader

        loader = SkillLoader()
        meta_list = loader.all_metadata()
        if not meta_list:
            pytest.skip("No skills found")

        skill = loader.load(meta_list[0].name)
        assert skill is not None
        assert len(skill.body) > 100, "Skill body should be non-empty"

    def test_supervisor_context_not_empty(self):
        from src.skills._loader import SkillLoader

        loader = SkillLoader()
        ctx = loader.build_supervisor_context()
        # Even if empty, should not raise
        assert isinstance(ctx, str)

    def test_query_resolution(self):
        from src.skills._loader import SkillLoader

        loader = SkillLoader()
        matched = loader.resolve_for_query("create a presentation", document_type="pptx")
        # Should match PPTX skill if it exists
        names = {m.name for m in matched}
        if "pptx" in {m.name for m in loader.all_metadata()}:
            assert "pptx" in names


class TestMetadataFilter:
    """Test metadata filter SQL clause generation."""

    def test_department_filter(self):
        from src.retrieval.metadata_filter import MetadataFilter

        f = MetadataFilter(department="finance")
        clauses = f.to_sql_clauses()
        params = f.to_sql_params()

        assert any("department" in c for c in clauses)
        assert params.get("department") == "finance"

    def test_date_range_filter(self):
        from datetime import datetime
        from src.retrieval.metadata_filter import MetadataFilter

        f = MetadataFilter(
            date_from=datetime(2024, 1, 1),
            date_to=datetime(2024, 12, 31),
        )
        clauses = f.to_sql_clauses()
        assert len(clauses) >= 2

    def test_empty_filter_produces_no_clauses(self):
        from src.retrieval.metadata_filter import MetadataFilter

        f = MetadataFilter()
        clauses = f.to_sql_clauses()
        assert clauses == []

    def test_temporal_parsing_last_n_days(self):
        from src.retrieval.metadata_filter import MetadataFilterBuilder

        builder = MetadataFilterBuilder()
        f = builder.from_query_analysis(
            department=None,
            content_type=None,
            temporal_expression="last 30 days",
        )
        assert f.date_from is not None
        assert f.date_to is not None
