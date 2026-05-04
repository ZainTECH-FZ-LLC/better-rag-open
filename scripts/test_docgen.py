"""
Test LLM-driven document generation directly, bypassing Celery.

The LLM analyses the user's request and builds the document spec,
then the appropriate generator (PPTX/DOCX/XLSX) produces the file.

Usage:
    python scripts/test_docgen.py "generate me a pptx on gen AI"
    python scripts/test_docgen.py "create a word doc summarising RAG architecture" --type docx
    python scripts/test_docgen.py "make an excel tracker for AI use cases" --type xlsx
    python scripts/test_docgen.py "quarterly report on LLMs" --output /tmp/out
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def run(user_request: str, doc_type: str, output_dir: Path) -> None:
    from src.document_generation.generator_factory import generate_document

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nRequest    : {user_request}")
    print(f"Doc type   : {doc_type.upper()}")
    print(f"Output dir : {output_dir}")
    print(f"\nBuilding spec with LLM...")

    result = await generate_document(
        doc_type=doc_type,
        user_request=user_request,
        context_chunks=[],  # no RAG context — LLM uses its own knowledge
    )

    print(f"Done!")
    print(f"  File      : {result.filename}")
    print(f"  Path      : {result.filepath}")
    print(f"  MIME type : {result.mime_type}")
    print(f"  Size      : {result.size_bytes:,} bytes")
    if result.metadata:
        for k, v in result.metadata.items():
            print(f"  {k:<10}: {v}")
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test LLM-driven document generation.")
    parser.add_argument("request", help="User request, e.g. 'generate me a pptx on gen AI'")
    parser.add_argument("--type", choices=["pptx", "docx", "xlsx"], default="pptx",
                        help="Document type to generate (default: pptx)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output directory (default: <repo>/generated)")
    args = parser.parse_args()

    if args.output:
        out_dir = args.output
    else:
        from config.settings import get_settings
        out_dir = get_settings().GENERATED_DIR

    asyncio.run(run(args.request, args.type, out_dir))
