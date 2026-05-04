"""Document generation tool — PPTX/DOCX/XLSX generation with skill context injection."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import tool

from config.settings import get_settings

logger = structlog.get_logger()


def create_doc_gen_tools(department: str) -> list:
    """
    Create document generation tools with department context.

    Each tool is backed by the appropriate generator from src/document_generation/.
    Department templates are resolved automatically.
    """
    settings = get_settings()

    @tool
    async def generate_pptx(
        topic: str,
        slide_outline: list[dict[str, Any]],
        use_template: str | None = None,
    ) -> dict[str, str]:
        """
        Generate a PowerPoint presentation (.pptx).

        Args:
            topic: Presentation title/topic.
            slide_outline: List of slide specs. Each dict must have:
                          - title: slide title (str)
                          - content_type: "bullets" | "chart" | "table" | "image"
                          - content: slide body content (str for bullets, dict for chart/table)
            use_template: Optional template name (e.g. "sales_deck", "quarterly_review").
                         If None, creates from scratch using department defaults.

        Returns:
            {"filename": "...", "download_url": "...", "mime_type": "..."}
        """
        from src.document_generation.pptx_generator import PptxGenerator
        from src.document_generation.base import DocumentSpec

        output_dir = settings.GENERATED_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        spec = DocumentSpec(
            doc_type="pptx",
            title=topic,
            sections=slide_outline,
            style=department,
            template_name=use_template,
        )

        generator = PptxGenerator()
        generated = await generator.generate(spec, output_dir)

        download_url = f"/api/v1/files/generated/{generated.filename}"
        logger.info("doc_gen_tool.pptx_generated", filename=generated.filename)

        return {
            "filename": generated.filename,
            "download_url": download_url,
            "mime_type": generated.mime_type,
            "filepath": str(generated.filepath),
        }

    @tool
    async def generate_docx(
        title: str,
        sections: list[dict[str, Any]],
        use_template: str | None = None,
    ) -> dict[str, str]:
        """
        Generate a Word document (.docx).

        Args:
            title: Document title.
            sections: List of section specs. Each dict must have:
                      - heading: section heading (str)
                      - content: section body text (str)
                      - level: heading level 1-3 (int)
            use_template: Optional template name (e.g. "policy_report", "financial_report").

        Returns:
            {"filename": "...", "download_url": "...", "mime_type": "..."}
        """
        from src.document_generation.docx_generator import DocxGenerator
        from src.document_generation.base import DocumentSpec

        output_dir = settings.GENERATED_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        spec = DocumentSpec(
            doc_type="docx",
            title=title,
            sections=sections,
            style=department,
            template_name=use_template,
        )

        generator = DocxGenerator()
        generated = await generator.generate(spec, output_dir)

        download_url = f"/api/v1/files/generated/{generated.filename}"
        logger.info("doc_gen_tool.docx_generated", filename=generated.filename)

        return {
            "filename": generated.filename,
            "download_url": download_url,
            "mime_type": generated.mime_type,
            "filepath": str(generated.filepath),
        }

    @tool
    async def generate_xlsx(
        title: str,
        sheets: list[dict[str, Any]],
        use_template: str | None = None,
    ) -> dict[str, str]:
        """
        Generate an Excel spreadsheet (.xlsx).

        Args:
            title: Workbook title / filename base.
            sheets: List of sheet specs. Each dict must have:
                    - name: sheet name (str)
                    - headers: column headers (list[str])
                    - rows: data rows (list[list[str]])
                    - formulas: optional dict mapping cell addresses to formula strings
                    - charts: optional list of chart specs
            use_template: Optional template name (e.g. "budget_template", "pipeline_tracker").

        Returns:
            {"filename": "...", "download_url": "...", "mime_type": "..."}
        """
        from src.document_generation.xlsx_generator import XlsxGenerator
        from src.document_generation.base import DocumentSpec

        output_dir = settings.GENERATED_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        spec = DocumentSpec(
            doc_type="xlsx",
            title=title,
            sections=sheets,
            style=department,
            template_name=use_template,
        )

        generator = XlsxGenerator()
        generated = await generator.generate(spec, output_dir)

        download_url = f"/api/v1/files/generated/{generated.filename}"
        logger.info("doc_gen_tool.xlsx_generated", filename=generated.filename)

        return {
            "filename": generated.filename,
            "download_url": download_url,
            "mime_type": generated.mime_type,
            "filepath": str(generated.filepath),
        }

    return [generate_pptx, generate_docx, generate_xlsx]
