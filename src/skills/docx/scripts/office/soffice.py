"""LibreOffice headless CLI wrapper for DOCX conversion and export."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _soffice_binary() -> str:
    """Locate the LibreOffice binary."""
    candidates = [
        "soffice",
        "libreoffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
    ]
    for candidate in candidates:
        if shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        "LibreOffice not found. Install with: apt-get install -y libreoffice"
    )


def convert_to_pdf(docx_path: Path, output_dir: Path | None = None) -> Path:
    """
    Convert a DOCX file to PDF using LibreOffice headless.

    Args:
        docx_path: Input .docx file.
        output_dir: Directory for the output PDF. Defaults to same dir as input.

    Returns:
        Path to the generated .pdf file.
    """
    docx_path = Path(docx_path).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else docx_path.parent

    subprocess.run(
        [
            _soffice_binary(),
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(docx_path),
        ],
        check=True,
        timeout=120,
        capture_output=True,
    )

    pdf_path = output_dir / docx_path.with_suffix(".pdf").name
    if not pdf_path.exists():
        raise RuntimeError(f"LibreOffice conversion failed: {pdf_path} not created")

    return pdf_path


def convert_to_html(docx_path: Path, output_dir: Path | None = None) -> Path:
    """
    Convert DOCX to HTML for lightweight inspection / diffing.

    Args:
        docx_path: Input .docx file.
        output_dir: Directory for the HTML output.

    Returns:
        Path to the generated .html file.
    """
    docx_path = Path(docx_path).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else docx_path.parent

    subprocess.run(
        [
            _soffice_binary(),
            "--headless",
            "--convert-to", "html",
            "--outdir", str(output_dir),
            str(docx_path),
        ],
        check=True,
        timeout=120,
        capture_output=True,
    )

    html_path = output_dir / docx_path.with_suffix(".html").name
    if not html_path.exists():
        raise RuntimeError(f"HTML conversion failed: {html_path} not created")

    return html_path


def accept_tracked_changes(docx_path: Path, output_path: Path | None = None) -> Path:
    """
    Accept all tracked changes in a DOCX by round-tripping through LibreOffice
    with the --infilter option (Writer macro not available in headless; use
    accept_changes.py for Python-based acceptance instead).

    This variant uses LibreOffice's built-in track-changes acceptance on save.
    """
    docx_path = Path(docx_path).resolve()
    output_path = output_path or docx_path.parent / f"{docx_path.stem}_accepted.docx"

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                _soffice_binary(),
                "--headless",
                "--convert-to", "docx",
                "--infilter=writer8",
                "--outdir", tmp,
                str(docx_path),
            ],
            check=True,
            timeout=120,
            capture_output=True,
        )
        converted = Path(tmp) / docx_path.with_suffix(".docx").name
        if converted.exists():
            shutil.copy2(converted, output_path)

    return Path(output_path)


def validate_docx(docx_path: Path) -> bool:
    """
    Attempt a dry-run conversion to verify the DOCX is well-formed.

    Returns True if LibreOffice can open the file without errors.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                _soffice_binary(),
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmp,
                str(Path(docx_path).resolve()),
            ],
            timeout=60,
            capture_output=True,
        )
        return result.returncode == 0
