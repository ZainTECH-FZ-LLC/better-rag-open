"""Accept all tracked changes in a DOCX file programmatically."""

from __future__ import annotations

import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path


def accept_all_changes(input_path: Path | str, output_path: Path | str | None = None) -> Path:
    """
    Remove all w:ins/w:del tracked-change markup, keeping inserted text
    and discarding deleted text, producing a clean document.

    Args:
        input_path: Source .docx file.
        output_path: Destination .docx (overwrites input if None).

    Returns:
        Path to the output file.
    """
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path

    with zipfile.ZipFile(input_path, "r") as z:
        names = z.namelist()
        files: dict[str, bytes] = {n: z.read(n) for n in names}

    if "word/document.xml" not in files:
        raise ValueError("Not a valid .docx file — word/document.xml not found")

    xml = files["word/document.xml"].decode("utf-8")

    # Accept insertions: unwrap <w:ins ...>...</w:ins> keeping inner content
    xml = re.sub(r"<w:ins\b[^>]*>(.*?)</w:ins>", r"\1", xml, flags=re.DOTALL)

    # Reject deletions: remove entire <w:del ...>...</w:del> block
    xml = re.sub(r"<w:del\b[^>]*>.*?</w:del>", "", xml, flags=re.DOTALL)

    # Clean up rPrChange elements (formatting change history)
    xml = re.sub(r"<w:rPrChange\b[^>]*>.*?</w:rPrChange>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"<w:pPrChange\b[^>]*>.*?</w:pPrChange>", "", xml, flags=re.DOTALL)

    files["word/document.xml"] = xml.encode("utf-8")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)

    output_path.write_bytes(buf.getvalue())
    print(f"Accepted all changes: {input_path} → {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: accept_changes.py input.docx [output.docx]")
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    accept_all_changes(src, dst)
