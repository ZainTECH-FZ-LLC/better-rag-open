"""Parser factory — routes files to the appropriate parser by type."""

from __future__ import annotations

from src.processing.parsers.base import DocumentParser
from src.processing.parsers.docx_parser import DOCXParser
from src.processing.parsers.pdf_parser import PDFParser
from src.processing.parsers.pptx_parser import PPTXParser
from src.processing.parsers.xlsb_parser import XLSBParser
from src.processing.parsers.xlsx_parser import XLSXParser

_PARSERS: dict[str, type[DocumentParser]] = {
    "pdf": PDFParser,
    "docx": DOCXParser,
    "pptx": PPTXParser,
    "xlsx": XLSXParser,
    "xlsb": XLSBParser,
}


def get_parser(file_type: str) -> DocumentParser:
    """Get the appropriate parser for a file type."""
    parser_cls = _PARSERS.get(file_type.lower())
    if parser_cls is None:
        raise ValueError(f"Unsupported file type: {file_type}")
    return parser_cls()
