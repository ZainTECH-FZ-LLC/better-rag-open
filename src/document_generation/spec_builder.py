"""LLM-driven document spec builder — turns user requests + RAG context into structured DocumentSpecs."""

from __future__ import annotations

import json

import structlog

from config.settings import get_settings
from src.document_generation.base import DocumentSpec

logger = structlog.get_logger()


class SpecBuilder:
    """
    Uses LLM to build a structured DocumentSpec from the user's request
    and retrieved RAG context.

    The spec is then passed to the appropriate generator (PPTX/DOCX/XLSX).
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def build(
        self,
        doc_type: str,
        user_request: str,
        context_chunks: list[dict],
    ) -> DocumentSpec:
        """
        Build a DocumentSpec by analyzing the user request and context.

        Args:
            doc_type: Target format (pptx, docx, xlsx).
            user_request: The user's original query/request.
            context_chunks: Retrieved RAG context to inform the document content.
        """
        context_text = "\n\n".join(
            f"Source: {c.get('document_title', 'doc')}\n{c.get('content', '')}"
            for c in context_chunks[:8]
        )

        if doc_type == "pptx":
            return await self._build_pptx_spec(user_request, context_text)
        elif doc_type == "docx":
            return await self._build_docx_spec(user_request, context_text)
        elif doc_type == "xlsx":
            return await self._build_xlsx_spec(user_request, context_text)
        else:
            raise ValueError(f"Unsupported doc_type: {doc_type}")

    async def _build_pptx_spec(self, request: str, context: str) -> DocumentSpec:
        prompt = f"""You are generating a PowerPoint presentation specification.
Based on the user's request and the context documents below, create a JSON spec.

User request: {request}

Context:
{context[:6000]}

Return a JSON object with:
- "title": presentation title
- "subtitle": subtitle for title slide
- "sections": list of slide objects, each with:
  - "type": "content" | "two_column" | "table" | "section" | "chart"
  - "title": slide title
  - "bullets": list of bullet points (for content type)
  - "left_column", "right_column": lists (for two_column type)
  - "headers", "rows": table data (for table type)
  - "chart": {{"type": "bar"|"line"|"pie", "labels": [...], "values": [...]}} (for chart type)

Create 6-10 slides. Use specific data from the context. Return ONLY valid JSON."""

        data = await self._call_llm(prompt)
        return DocumentSpec(
            doc_type="pptx",
            title=data.get("title", "Presentation"),
            sections=data.get("sections", []),
            data={"subtitle": data.get("subtitle", "")},
        )

    async def _build_docx_spec(self, request: str, context: str) -> DocumentSpec:
        prompt = f"""You are generating a Word document specification.
Based on the user's request and context documents, create a JSON spec.

User request: {request}

Context:
{context[:6000]}

Return a JSON object with:
- "title": document title
- "subtitle": optional subtitle
- "sections": list of section objects, each with:
  - "type": "heading" | "paragraph" | "bullets" | "numbered_list" | "table"
  - "title": section heading (for heading, bullets, table types)
  - "level": heading level 1-3 (for heading type)
  - "text": body text (for paragraph type)
  - "items": list of items (for bullets/numbered_list)
  - "headers", "rows": table data (for table type)

Create comprehensive content. Use specific data from the context. Return ONLY valid JSON."""

        data = await self._call_llm(prompt)
        return DocumentSpec(
            doc_type="docx",
            title=data.get("title", "Document"),
            sections=data.get("sections", []),
            data={"subtitle": data.get("subtitle", "")},
        )

    async def _build_xlsx_spec(self, request: str, context: str) -> DocumentSpec:
        prompt = f"""You are generating an Excel spreadsheet specification.
Based on the user's request and context documents, create a JSON spec.

User request: {request}

Context:
{context[:6000]}

Return a JSON object with:
- "title": spreadsheet title
- "sections": list of sheet objects, each with:
  - "type": "sheet" | "chart_sheet"
  - "title": sheet name (max 31 chars)
  - "headers": list of column headers
  - "rows": list of row arrays with data values
  - "formulas": optional object mapping column letters to Excel formulas (e.g. {{"C": "=SUM(C2:C10)"}})
  - "chart": {{"type": "bar"|"line"|"pie", "title": "..."}} (for chart_sheet type)

Use specific numbers from the context. Return ONLY valid JSON."""

        data = await self._call_llm(prompt)
        return DocumentSpec(
            doc_type="xlsx",
            title=data.get("title", "Spreadsheet"),
            sections=data.get("sections", []),
        )

    async def _call_llm(self, prompt: str) -> dict:
        """Call the LLM and parse JSON response."""
        from langchain_openai import AzureChatOpenAI

        llm = AzureChatOpenAI(
            azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
            api_key=self.settings.AZURE_OPENAI_API_KEY,
            api_version=self.settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=self.settings.LLM_EXPENSIVE_MODEL,
            max_tokens=3000,
        )

        response = await llm.ainvoke(prompt)
        content = response.content.strip()

        # Parse JSON
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error("spec_builder.json_parse_failed", content=content[:500])
            return {"title": "Generated Document", "sections": []}
