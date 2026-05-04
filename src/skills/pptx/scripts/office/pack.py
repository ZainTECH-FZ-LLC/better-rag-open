"""Repack a directory of PPTX XML parts back into a .pptx file."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def pack(source_dir: Path | str, output_pptx: Path | str) -> Path:
    """ZIP a directory into a .pptx file with correct MIME ordering."""
    source_dir = Path(source_dir)
    output_pptx = Path(output_pptx)

    with zipfile.ZipFile(output_pptx, "w", zipfile.ZIP_DEFLATED) as z:
        # [Content_Types].xml must be first in the ZIP
        ct = source_dir / "[Content_Types].xml"
        if ct.exists():
            z.write(ct, "[Content_Types].xml")

        for file in sorted(source_dir.rglob("*")):
            if file.is_file() and file.name != "[Content_Types].xml":
                arcname = file.relative_to(source_dir)
                z.write(file, arcname)

    print(f"Packed {source_dir} → {output_pptx}")
    return output_pptx


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: pack.py source_dir/ output.pptx")
        sys.exit(1)
    pack(sys.argv[1], sys.argv[2])
