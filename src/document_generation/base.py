"""Abstract document generator interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GeneratedDocument:
    """Output from a document generator."""

    filename: str
    filepath: Path
    mime_type: str
    file_type: str  # pptx, docx, xlsx
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentSpec:
    """Specification for document generation, built from LLM analysis of user request."""

    doc_type: str  # pptx, docx, xlsx
    title: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    style: str = "professional"
    template_name: str | None = None
    charts: list[dict[str, Any]] = field(default_factory=list)


class DocumentGenerator(ABC):
    """Base class for file-type-specific document generators."""

    @abstractmethod
    async def generate(self, spec: DocumentSpec, output_dir: Path) -> GeneratedDocument:
        """Generate a document from a specification."""
        ...
