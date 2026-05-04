"""DOCX structural validation — catches common generation errors before delivery."""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


# Required parts that must exist in a valid DOCX
_REQUIRED_PARTS = {
    "[Content_Types].xml",
    "word/document.xml",
    "_rels/.rels",
    "word/_rels/document.xml.rels",
}

# Word namespace
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "VALID" if self.valid else "INVALID"
        lines = [f"[{status}]"]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN:  {w}")
        return "\n".join(lines)


def validate(docx_path: Path) -> ValidationResult:
    """
    Perform structural validation on a .docx file.

    Checks performed:
    1. File is a valid ZIP archive.
    2. All required OOXML parts are present.
    3. ``word/document.xml`` is well-formed XML.
    4. Document body element exists.
    5. Paragraph count is non-zero (catches empty-body generation failures).
    6. No broken image relationships (warn only).

    Args:
        docx_path: Path to the .docx file.

    Returns:
        ValidationResult with ``valid``, ``errors``, and ``warnings``.
    """
    docx_path = Path(docx_path)
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. ZIP integrity ────────────────────────────────────────────────────
    if not docx_path.exists():
        return ValidationResult(valid=False, errors=[f"File not found: {docx_path}"])

    try:
        zf = zipfile.ZipFile(docx_path, "r")
    except zipfile.BadZipFile as exc:
        return ValidationResult(valid=False, errors=[f"Not a valid ZIP/DOCX: {exc}"])

    with zf:
        parts = set(zf.namelist())

        # ── 2. Required parts ───────────────────────────────────────────────
        for required in _REQUIRED_PARTS:
            if required not in parts:
                errors.append(f"Missing required part: {required}")

        if errors:
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        # ── 3. XML well-formedness ──────────────────────────────────────────
        try:
            doc_xml = zf.read("word/document.xml")
            root = ET.fromstring(doc_xml)
        except ET.ParseError as exc:
            errors.append(f"word/document.xml is malformed XML: {exc}")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        # ── 4. Body element ─────────────────────────────────────────────────
        ns = {"w": _W}
        body = root.find("w:body", ns)
        if body is None:
            errors.append("word/document.xml: <w:body> element not found")

        # ── 5. Paragraph count ──────────────────────────────────────────────
        paragraphs = root.findall(".//w:p", ns)
        if len(paragraphs) == 0:
            errors.append("Document body contains no paragraphs (empty generation?)")
        elif len(paragraphs) < 3:
            warnings.append(
                f"Very few paragraphs ({len(paragraphs)}) — document may be truncated"
            )

        # ── 6. Image relationships ──────────────────────────────────────────
        if "word/_rels/document.xml.rels" in parts:
            try:
                rels_xml = zf.read("word/_rels/document.xml.rels")
                rels_root = ET.fromstring(rels_xml)
                for rel in rels_root:
                    if "image" in rel.get("Type", "").lower():
                        target = rel.get("Target", "")
                        # Internal images are under word/media/
                        if not target.startswith("http") and target not in parts:
                            full = f"word/{target.lstrip('/')}"
                            if full not in parts:
                                warnings.append(f"Broken image relationship: {target}")
            except ET.ParseError:
                warnings.append("Could not parse document.xml.rels for relationship check")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
