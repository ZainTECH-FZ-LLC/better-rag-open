"""Add review comments to a DOCX file programmatically."""

from __future__ import annotations

import sys
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

# Word namespace map
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

COMMENT_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def add_comment(
    input_path: Path | str,
    output_path: Path | str,
    paragraph_index: int,
    comment_text: str,
    author: str = "BetterRAG",
    date: str = "2024-01-01T00:00:00Z",
) -> Path:
    """
    Insert a comment on the specified paragraph in a DOCX.

    Args:
        input_path: Source .docx file.
        output_path: Destination .docx.
        paragraph_index: 0-based index of the paragraph to comment on.
        comment_text: The comment body text.
        author: Comment author display name.
        date: ISO 8601 timestamp for the comment.

    Returns:
        Path to the modified .docx file.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    comment_id = str(abs(hash(comment_text)) % 100000)

    with zipfile.ZipFile(input_path, "r") as z:
        names = z.namelist()
        files: dict[str, bytes] = {n: z.read(n) for n in names}

    # ── Build comments.xml ────────────────────────────────────────────────────
    comments_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{COMMENT_NS}">'
        f'<w:comment w:id="{comment_id}" w:author="{author}" w:date="{date}">'
        f'<w:p><w:r><w:t>{comment_text}</w:t></w:r></w:p>'
        f"</w:comment></w:comments>"
    )
    files["word/comments.xml"] = comments_xml.encode("utf-8")

    # ── Inject comment markers into document.xml ──────────────────────────────
    doc_xml = files.get("word/document.xml", b"").decode("utf-8")

    # Find paragraph tags — simple text marker injection
    para_marker = f'<w:commentRangeStart w:id="{comment_id}"/>'
    para_end = f'<w:commentRangeEnd w:id="{comment_id}"/><w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="{comment_id}"/></w:r>'

    parts = doc_xml.split("<w:p ")
    if paragraph_index + 1 < len(parts):
        parts[paragraph_index + 1] = para_marker + "<w:p " + parts[paragraph_index + 1]
        # append end marker before closing </w:p>
        idx_close = parts[paragraph_index + 1].find("</w:p>")
        if idx_close != -1:
            parts[paragraph_index + 1] = (
                parts[paragraph_index + 1][:idx_close]
                + para_end
                + parts[paragraph_index + 1][idx_close:]
            )

    files["word/document.xml"] = "<w:p ".join(parts).encode("utf-8")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    output_path.write_bytes(buf.getvalue())
    print(f"Added comment on paragraph {paragraph_index}: {input_path} → {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: comment.py input.docx output.docx PARA_INDEX 'comment text'")
        sys.exit(1)
    add_comment(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])
