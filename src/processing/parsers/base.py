"""Abstract document parser interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParsedDocument:
    """Unified output from any file-type parser."""

    text: str = ""
    markdown: str = ""
    sections: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    page_count: int = 0
    word_count: int = 0
    file_properties: dict = field(default_factory=dict)
    # PPTX-specific
    slides: list[dict] = field(default_factory=list)
    # XLSX-specific
    sheets: list[dict] = field(default_factory=list)
    # PDF vision-specific: one dict per page with keys index, text, vision_text
    pages: list[dict] = field(default_factory=list)


class DocumentParser(ABC):
    """Base class for file-type-specific parsers."""

    @abstractmethod
    async def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        """Parse a document and return structured content."""
        ...
