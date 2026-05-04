"""Generate PNG thumbnails from PPTX slides using LibreOffice headless."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path


async def generate_thumbnails(
    pptx_path: Path | str,
    output_dir: Path | str,
    width: int = 400,
) -> list[Path]:
    """
    Render each slide of a PPTX to a PNG thumbnail.

    Requires LibreOffice to be installed and on PATH.

    Args:
        pptx_path: Path to the .pptx file.
        output_dir: Directory to write thumbnail PNGs.
        width: Output image width in pixels (height is auto-calculated).

    Returns:
        List of generated PNG paths, one per slide.
    """
    pptx_path = Path(pptx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # LibreOffice headless → export to images
    cmd = [
        "soffice",
        "--headless",
        "--norestore",
        f"--convert-to", "png",
        "--outdir", str(output_dir),
        str(pptx_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed: {stderr.decode()}"
        )

    # LibreOffice names output as stem + ".png" for single files,
    # or stem + "-{N}.png" for multi-page. Collect them.
    stem = pptx_path.stem
    thumbnails = sorted(output_dir.glob(f"{stem}*.png"))
    return thumbnails


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: thumbnail.py input.pptx output_dir/ [--width N]")
        sys.exit(1)

    pptx = Path(sys.argv[1])
    out = Path(sys.argv[2])
    w = 400
    if "--width" in sys.argv:
        idx = sys.argv.index("--width")
        w = int(sys.argv[idx + 1])

    results = asyncio.run(generate_thumbnails(pptx, out, w))
    for r in results:
        print(r)
