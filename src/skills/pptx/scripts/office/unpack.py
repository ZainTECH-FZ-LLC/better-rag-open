"""Unpack a PPTX (ZIP) into a directory for XML editing."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def unpack(pptx_path: Path | str, output_dir: Path | str) -> Path:
    """Extract PPTX contents to a directory."""
    pptx_path = Path(pptx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(pptx_path, "r") as z:
        z.extractall(output_dir)

    print(f"Unpacked {pptx_path} → {output_dir}")
    return output_dir


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: unpack.py input.pptx output_dir/")
        sys.exit(1)
    unpack(sys.argv[1], sys.argv[2])
