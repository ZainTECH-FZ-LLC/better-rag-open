"""PPTX → PDF → PNG slide renderer using LibreOffice + pymupdf.

Converts a PPTX file to individual slide images by:
1. Writing PPTX bytes to a temp file
2. Converting to PDF via LibreOffice headless (WSL on Windows, native on Linux)
3. Rendering each PDF page as a high-res PNG with pymupdf
"""

from __future__ import annotations

import asyncio
import platform
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger()

# DPI for rendering — 200 gives ~1920px wide slides (good detail for vision models)
RENDER_DPI = 200


async def render_slides(file_bytes: bytes, filename: str) -> list[bytes]:
    """Convert PPTX to a list of PNG images (one per slide).

    Args:
        file_bytes: Raw PPTX file content.
        filename: Original filename (for logging).

    Returns:
        List of PNG bytes, one per slide in order.

    Raises:
        RuntimeError: If LibreOffice conversion fails.
    """
    return await asyncio.to_thread(_render_sync, file_bytes, filename)


def _render_sync(file_bytes: bytes, filename: str) -> list[bytes]:
    import fitz  # pymupdf

    # Suppress noisy MuPDF structure tree warnings from LibreOffice-generated PDFs
    fitz.TOOLS.mupdf_warnings(False)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Write PPTX to temp file
        pptx_path = tmp_path / "input.pptx"
        pptx_path.write_bytes(file_bytes)

        # Convert PPTX → PDF via LibreOffice
        pdf_path = _libreoffice_convert(pptx_path, tmp_path)

        # Render each PDF page as PNG
        doc = fitz.open(str(pdf_path))
        images: list[bytes] = []
        for page in doc:
            mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
        doc.close()

        logger.info(
            "slide_renderer.rendered",
            filename=filename,
            slides=len(images),
            dpi=RENDER_DPI,
        )
        return images


def _libreoffice_convert(pptx_path: Path, output_dir: Path) -> Path:
    """Run LibreOffice headless to convert PPTX to PDF.

    Handles Windows (via WSL) vs Linux (native) automatically.
    """
    import subprocess

    is_windows = platform.system() == "Windows"

    if is_windows:
        # Convert Windows paths to WSL paths
        wsl_input = _win_to_wsl_path(pptx_path)
        wsl_output = _win_to_wsl_path(output_dir)
        cmd = [
            "wsl", "libreoffice",
            "--headless", "--convert-to", "pdf",
            "--outdir", wsl_output,
            wsl_input,
        ]
    else:
        cmd = [
            "libreoffice",
            "--headless", "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(pptx_path),
        ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,  # 2 min timeout for large files
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed (rc={result.returncode}): {result.stderr}"
        )

    # Find the output PDF
    pdf_path = output_dir / "input.pdf"
    if not pdf_path.exists():
        # Sometimes LibreOffice names it differently — find any PDF
        pdfs = list(output_dir.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(
                f"LibreOffice produced no PDF. stdout={result.stdout}, stderr={result.stderr}"
            )
        pdf_path = pdfs[0]

    return pdf_path


def _win_to_wsl_path(path: Path) -> str:
    """Convert a Windows path like C:\\Users\\foo to /mnt/c/Users/foo for WSL."""
    s = str(path.resolve())
    # e.g. C:\Temp\foo → /mnt/c/Temp/foo
    drive = s[0].lower()
    rest = s[2:].replace("\\", "/")
    return f"/mnt/{drive}{rest}"
