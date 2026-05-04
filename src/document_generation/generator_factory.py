"""Document generator factory — routes to the appropriate generator by doc_type."""

from __future__ import annotations

from pathlib import Path

import structlog

from config.settings import get_settings
from src.document_generation.base import DocumentGenerator, DocumentSpec, GeneratedDocument
from src.document_generation.docx_generator import DOCXGenerator
from src.document_generation.pptx_generator import PPTXGenerator
from src.document_generation.spec_builder import SpecBuilder
from src.document_generation.xlsx_generator import XLSXGenerator

logger = structlog.get_logger()

_GENERATORS: dict[str, type[DocumentGenerator]] = {
    "pptx": PPTXGenerator,
    "docx": DOCXGenerator,
    "xlsx": XLSXGenerator,
}


def get_generator(doc_type: str) -> DocumentGenerator:
    """Get the appropriate generator for a document type."""
    gen_cls = _GENERATORS.get(doc_type.lower())
    if gen_cls is None:
        raise ValueError(f"Unsupported document type: {doc_type}")
    return gen_cls()


async def generate_document(
    doc_type: str,
    user_request: str,
    context_chunks: list[dict],
) -> GeneratedDocument:
    """
    End-to-end document generation:
    1. Build spec from user request + RAG context
    2. Generate document
    3. Return file info

    This is the main entry point for the doc generation pipeline.
    """
    settings = get_settings()
    output_dir = settings.GENERATED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build spec
    builder = SpecBuilder()
    spec = await builder.build(doc_type, user_request, context_chunks)

    # Generate
    generator = get_generator(doc_type)
    result = await generator.generate(spec, output_dir)

    logger.info(
        "document_generation.complete",
        doc_type=doc_type,
        filename=result.filename,
        size=result.size_bytes,
    )
    return result
