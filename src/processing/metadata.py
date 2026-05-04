"""Metadata extractor — merges Graph API, file properties, and LLM-derived metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from config.settings import get_settings

logger = structlog.get_logger()


@dataclass
class DocumentMetadata:
    """Merged metadata from all sources."""

    # From Graph API (highest priority)
    author: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    modified_by: str | None = None
    sharepoint_url: str | None = None
    site_name: str | None = None
    library_name: str | None = None

    # From file properties
    page_count: int = 0
    word_count: int = 0
    title: str | None = None

    # LLM-derived
    department: str | None = None
    content_type: str | None = None
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    language: str = "en"


class MetadataExtractor:
    """Extracts and merges metadata from 3 sources."""

    _llm = None  # shared across all instances

    def __init__(self) -> None:
        self.settings = get_settings()

    async def extract(
        self,
        graph_metadata: dict | None,
        file_properties: dict | None,
        text_content: str,
    ) -> DocumentMetadata:
        """
        Extract metadata by merging:
        1. Graph API metadata (author, dates, URLs)
        2. File properties (page count, word count, title)
        3. LLM-derived metadata (department, topics, content type)
        """
        meta = DocumentMetadata()

        # Source 1: Graph API
        if graph_metadata:
            meta.author = graph_metadata.get("created_by")
            meta.created_at = graph_metadata.get("created_at")
            meta.modified_at = graph_metadata.get("modified_at")
            meta.modified_by = graph_metadata.get("modified_by")
            meta.sharepoint_url = graph_metadata.get("sharepoint_url")
            meta.site_name = graph_metadata.get("site_name")
            meta.library_name = graph_metadata.get("library_name")

            # Try to infer department from site path
            parent_path = graph_metadata.get("parent_path", "")
            meta.department = _infer_department_from_path(parent_path)

        # Source 2: File properties
        if file_properties:
            meta.page_count = file_properties.get("page_count", 0)
            meta.word_count = file_properties.get("word_count", 0)
            meta.title = file_properties.get("title")

        # Source 3: LLM-derived (fills gaps)
        if text_content and len(text_content) > 100:
            llm_meta = await self._extract_with_llm(text_content[:4000])
            if not meta.department:
                meta.department = llm_meta.get("department")
            meta.content_type = llm_meta.get("content_type")
            meta.topics = llm_meta.get("topics", [])
            meta.language = llm_meta.get("language", "en")

        return meta

    def _get_llm(self):
        if MetadataExtractor._llm is None:
            from langchain_openai import AzureChatOpenAI
            # Use vision model (gpt-4.1-mini) — faster than reasoning models
            MetadataExtractor._llm = AzureChatOpenAI(
                azure_endpoint=self.settings.VISION_AZURE_ENDPOINT,
                api_key=self.settings.VISION_AZURE_API_KEY,
                api_version=self.settings.VISION_AZURE_API_VERSION,
                azure_deployment=self.settings.VISION_AZURE_DEPLOYMENT,
                max_tokens=4096,
            )
        return MetadataExtractor._llm

    async def _extract_with_llm(self, text_sample: str) -> dict:
        """Use cheap LLM to classify document metadata."""
        try:
            llm = self._get_llm()

            prompt = f"""Analyze this document excerpt and return JSON with:
- "department": one of "hr", "finance", "sales", "marketing", "general"
- "content_type": one of "policy", "report", "presentation", "memo", "spreadsheet", "proposal", "guide", "other"
- "topics": list of 3-5 key topics
- "language": ISO 639-1 code

Document excerpt:
{text_sample}

Return ONLY valid JSON, no other text."""

            response = await llm.ainvoke(prompt)
            content = response.content.strip()

            # Parse JSON from response
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            return json.loads(content)

        except Exception as e:
            logger.warn("metadata.llm_extraction_failed", error=str(e))
            return {}


def _infer_department_from_path(path: str) -> str | None:
    """Try to infer department from the SharePoint folder path."""
    path_lower = path.lower()
    departments = {
        "hr": ["hr", "human resources", "people", "talent"],
        "finance": ["finance", "accounting", "budget", "fiscal"],
        "sales": ["sales", "revenue", "deals", "pipeline"],
        "marketing": ["marketing", "brand", "campaign", "comms"],
    }
    for dept, keywords in departments.items():
        if any(kw in path_lower for kw in keywords):
            return dept
    return None
