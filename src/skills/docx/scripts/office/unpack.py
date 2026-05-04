"""Unpack a DOCX file into its constituent XML parts for direct editing."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def unpack(docx_path: Path, output_dir: Path | None = None) -> Path:
    """
    Extract a .docx archive into a directory of XML files.

    A .docx file is a ZIP archive containing:
    - word/document.xml       — main body content
    - word/styles.xml         — paragraph and character styles
    - word/numbering.xml      — list definitions
    - word/header*.xml        — headers
    - word/footer*.xml        — footers
    - word/comments.xml       — review comments
    - word/_rels/document.xml.rels  — relationships
    - [Content_Types].xml     — MIME type manifest
    - docProps/core.xml       — author/title metadata

    Args:
        docx_path: Source .docx file.
        output_dir: Directory to extract into. Created if missing.
                    Defaults to <docx_name>_unpacked/ next to the source file.

    Returns:
        Path to the extraction directory.
    """
    docx_path = Path(docx_path).resolve()

    if output_dir is None:
        output_dir = docx_path.parent / f"{docx_path.stem}_unpacked"

    output_dir = Path(output_dir)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    with zipfile.ZipFile(docx_path, "r") as zf:
        zf.extractall(output_dir)

    return output_dir


def list_parts(docx_path: Path) -> list[str]:
    """Return a sorted list of part paths inside the DOCX archive."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        return sorted(zf.namelist())


def read_part(docx_path: Path, part_path: str) -> str:
    """
    Read a single XML part from a DOCX without fully extracting.

    Args:
        docx_path: Path to the .docx file.
        part_path: Internal archive path, e.g. ``"word/document.xml"``.

    Returns:
        UTF-8 decoded content of the part.
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        return zf.read(part_path).decode("utf-8")
