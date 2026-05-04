"""LibreOffice headless CLI wrapper for PPTX conversion and export."""

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


def convert_to_pdf(pptx_path: Path, output_dir: Path | None = None) -> Path:
    """
    Convert a PPTX file to PDF using LibreOffice headless.

    Args:
        pptx_path: Input .pptx file.
        output_dir: Directory for the output PDF. Defaults to same dir as input.

    Returns:
        Path to the generated .pdf file.
    """
    pptx_path = Path(pptx_path).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else pptx_path.parent

    subprocess.run(
        [
            _soffice_binary(),
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(pptx_path),
        ],
        check=True,
        timeout=120,
        capture_output=True,
    )

    pdf_path = output_dir / pptx_path.with_suffix(".pdf").name
    if not pdf_path.exists():
        raise RuntimeError(f"LibreOffice conversion failed: {pdf_path} not created")

    return pdf_path


def convert_to_images(pptx_path: Path, output_dir: Path) -> list[Path]:
    """
    Export each slide as a PNG image.

    Converts PPTX → PDF → individual slide PNGs using LibreOffice + pdftoppm (poppler).

    Args:
        pptx_path: Input .pptx file.
        output_dir: Directory to write slide-N.png files.

    Returns:
        Sorted list of slide image paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        pdf = convert_to_pdf(pptx_path, output_dir=Path(tmp))

        # Try pdftoppm (poppler) for per-page PNG extraction
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r", "150",         # 150 DPI → ~1587×1190 for 16:9
                    str(pdf),
                    str(output_dir / "slide"),
                ],
                check=True,
                timeout=120,
                capture_output=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "pdftoppm not found. Install poppler-utils: apt-get install -y poppler-utils"
            )

    return sorted(output_dir.glob("slide-*.png"))


def validate_pptx(pptx_path: Path) -> bool:
    """
    Attempt a dry-run conversion to verify the PPTX is well-formed.

    Returns True if LibreOffice can open the file without errors.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                _soffice_binary(),
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmp,
                str(Path(pptx_path).resolve()),
            ],
            timeout=60,
            capture_output=True,
        )
        return result.returncode == 0
