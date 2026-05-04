"""Repack an unpacked DOCX directory back into a .docx archive."""

from __future__ import annotations

import zipfile
from pathlib import Path


# Parts that must appear first in the archive for Office compatibility
_PRIORITY_PARTS = ["[Content_Types].xml", "_rels/.rels"]


def pack(source_dir: Path, output_path: Path | None = None) -> Path:
    """
    Pack an unpacked DOCX directory back into a .docx file.

    The resulting file is a valid ZIP archive that Microsoft Word and
    LibreOffice can open. ``[Content_Types].xml`` is written first to
    satisfy Office's requirement for that entry.

    Args:
        source_dir: Directory produced by ``unpack()``.
        output_path: Destination .docx path.
                     Defaults to ``<source_dir_name>.docx`` in the parent directory.

    Returns:
        Path to the created .docx file.
    """
    source_dir = Path(source_dir).resolve()

    if output_path is None:
        stem = source_dir.name.removesuffix("_unpacked") or source_dir.name
        output_path = source_dir.parent / f"{stem}.docx"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_files = [p for p in source_dir.rglob("*") if p.is_file()]

    def _sort_key(p: Path) -> tuple[int, str]:
        rel = p.relative_to(source_dir).as_posix()
        for i, priority in enumerate(_PRIORITY_PARTS):
            if rel == priority:
                return (i, rel)
        return (len(_PRIORITY_PARTS), rel)

    all_files.sort(key=_sort_key)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in all_files:
            archive_name = file_path.relative_to(source_dir).as_posix()
            zf.write(file_path, archive_name)

    return output_path


def update_part(
    docx_path: Path,
    part_path: str,
    new_content: str | bytes,
    output_path: Path | None = None,
) -> Path:
    """
    Replace a single XML part inside a .docx without fully unpacking it.

    Args:
        docx_path: Source .docx file.
        part_path: Internal archive path, e.g. ``"word/document.xml"``.
        new_content: New content (str encoded to UTF-8, or raw bytes).
        output_path: Where to write the modified .docx.
                     Defaults to overwriting the source file.

    Returns:
        Path to the written .docx file.
    """
    if isinstance(new_content, str):
        new_content = new_content.encode("utf-8")

    output_path = output_path or docx_path

    import io, shutil, tempfile

    tmp = io.BytesIO()
    with zipfile.ZipFile(docx_path, "r") as zin:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == part_path:
                    zout.writestr(item, new_content)
                else:
                    zout.writestr(item, zin.read(item.filename))

    output_path = Path(output_path)
    output_path.write_bytes(tmp.getvalue())
    return output_path
