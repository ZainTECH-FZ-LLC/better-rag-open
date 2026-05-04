"""Visual QA pipeline — render → inspect → fix loop for generated documents."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from config.settings import get_settings

logger = structlog.get_logger()

settings = get_settings()


@dataclass
class QAIssue:
    """A single QA finding."""
    severity: str          # "error" | "warning" | "info"
    slide_or_page: int     # 0-based index (-1 = whole document)
    description: str
    auto_fixable: bool = False
    fix_hint: str = ""


@dataclass
class QAResult:
    """Output from a visual QA run."""
    passed: bool
    issues: list[QAIssue] = field(default_factory=list)
    iterations: int = 0
    final_path: Path | None = None

    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def summary(self) -> str:
        return (
            f"QA {'PASSED' if self.passed else 'FAILED'} "
            f"after {self.iterations} iteration(s): "
            f"{self.error_count()} errors, {self.warning_count()} warnings"
        )


class VisualQAPipeline:
    """
    Render-inspect-fix loop for generated Office documents.

    Workflow:
        1. Render document to images (thumbnail per slide/page)
        2. Send thumbnails to vision LLM for inspection
        3. Parse LLM findings into QAIssue list
        4. Apply auto-fixable corrections; re-generate if errors remain
        5. Repeat up to max_iterations
    """

    def __init__(self, max_iterations: int = 2) -> None:
        self.max_iterations = max_iterations

    async def run(
        self,
        doc_path: Path,
        doc_type: str,  # pptx, docx, xlsx
        spec: Any | None = None,
    ) -> QAResult:
        """
        Run the QA loop on a generated document.

        Args:
            doc_path: Path to the generated file.
            doc_type: File type string ("pptx", "docx", "xlsx").
            spec: Original DocumentSpec (for regeneration context).

        Returns:
            QAResult with pass/fail status and all issues found.
        """
        result = QAResult(passed=False, final_path=doc_path)
        current_path = doc_path

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            logger.info("visual_qa.iteration", iteration=iteration, path=str(current_path))

            # Step 1: render to thumbnails
            thumbnails = await self._render(current_path, doc_type)
            if not thumbnails:
                result.issues.append(QAIssue(
                    severity="error",
                    slide_or_page=-1,
                    description="Failed to render document to images",
                ))
                break

            # Step 2: inspect with vision LLM
            issues = await self._inspect(thumbnails, doc_type)
            result.issues = issues

            errors = [i for i in issues if i.severity == "error"]
            if not errors:
                result.passed = True
                result.final_path = current_path
                break

            # Step 3: attempt auto-fixes
            fixable = [i for i in errors if i.auto_fixable]
            if fixable and spec is not None and iteration < self.max_iterations:
                current_path = await self._auto_fix(current_path, doc_type, fixable, spec)
            else:
                # No more fix attempts — report failure
                break

        logger.info(
            "visual_qa.complete",
            passed=result.passed,
            iterations=result.iterations,
            errors=result.error_count(),
            warnings=result.warning_count(),
        )
        return result

    async def _render(self, doc_path: Path, doc_type: str) -> list[Path]:
        """Render document to PNG thumbnails."""
        thumb_dir = doc_path.parent / f".qa_thumbs_{doc_path.stem}"
        thumb_dir.mkdir(exist_ok=True)

        if doc_type == "pptx":
            try:
                from src.skills.pptx.scripts.thumbnail import generate_thumbnails
                return await generate_thumbnails(doc_path, thumb_dir, width=800)
            except Exception as exc:
                logger.warning("visual_qa.render_failed", error=str(exc))
                return []
        elif doc_type in ("docx", "xlsx"):
            # LibreOffice headless conversion to PNG
            return await self._libreoffice_render(doc_path, thumb_dir)
        return []

    async def _libreoffice_render(self, doc_path: Path, output_dir: Path) -> list[Path]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "soffice", "--headless", "--norestore",
                "--convert-to", "png",
                "--outdir", str(output_dir),
                str(doc_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                logger.warning("visual_qa.libreoffice_failed", stderr=stderr.decode()[:200])
                return []
            return sorted(output_dir.glob("*.png"))
        except (asyncio.TimeoutError, FileNotFoundError) as exc:
            logger.warning("visual_qa.libreoffice_unavailable", error=str(exc))
            return []

    async def _inspect(self, thumbnails: list[Path], doc_type: str) -> list[QAIssue]:
        """Send thumbnails to vision LLM for QA inspection."""
        if not thumbnails:
            return []

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic()

            image_blocks = []
            for i, thumb in enumerate(thumbnails[:6]):  # limit to first 6 pages
                img_data = thumb.read_bytes()
                b64 = base64.b64encode(img_data).decode()
                image_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    }
                })
                image_blocks.append({
                    "type": "text",
                    "text": f"[Slide/Page {i + 1}]"
                })

            prompt = _build_inspection_prompt(doc_type)
            image_blocks.append({"type": "text", "text": prompt})

            response = await client.messages.create(
                model=settings.CHEAP_MODEL,
                max_completion_tokens=1024,
                messages=[{"role": "user", "content": image_blocks}],
            )
            return _parse_inspection_response(response.content[0].text)

        except Exception as exc:
            logger.warning("visual_qa.inspection_failed", error=str(exc))
            return []

    async def _auto_fix(
        self,
        doc_path: Path,
        doc_type: str,
        issues: list[QAIssue],
        spec: Any,
    ) -> Path:
        """Attempt to auto-fix common issues and regenerate the document."""
        logger.info("visual_qa.auto_fix_start", issues=[i.description for i in issues])

        try:
            from src.document_generation.generator_factory import GeneratorFactory
            generator = GeneratorFactory.get(doc_type)
            output_dir = doc_path.parent
            # Re-generate with issue hints appended to spec
            if hasattr(spec, "data"):
                spec.data["qa_fix_hints"] = [i.fix_hint for i in issues if i.fix_hint]
            generated = await generator.generate(spec, output_dir)
            return generated.filepath
        except Exception as exc:
            logger.warning("visual_qa.auto_fix_failed", error=str(exc))
            return doc_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_inspection_prompt(doc_type: str) -> str:
    type_guidance = {
        "pptx": (
            "Check each slide for: overflow text, truncated content, missing visuals, "
            "alignment issues, inconsistent fonts/colors, blank slides, or placeholder text "
            "that wasn't replaced (e.g. 'Click to add title')."
        ),
        "docx": (
            "Check for: broken table formatting, missing content, placeholder text "
            "not replaced, heading hierarchy issues, or obvious layout problems."
        ),
        "xlsx": (
            "Check for: formula errors (#REF!, #DIV/0!, #VALUE!), empty cells that "
            "should have data, broken chart references, or column width overflow."
        ),
    }.get(doc_type, "Check for any obvious formatting or content issues.")

    return (
        f"You are a document QA inspector reviewing a generated {doc_type.upper()} file.\n\n"
        f"{type_guidance}\n\n"
        "For each issue found, respond with one line in this exact format:\n"
        "ISSUE|<severity>|<page_number>|<description>|<auto_fixable>|<fix_hint>\n\n"
        "Where:\n"
        "- severity: error, warning, or info\n"
        "- page_number: 1-based slide/page number, or 0 for document-level\n"
        "- description: brief description of the issue\n"
        "- auto_fixable: yes or no\n"
        "- fix_hint: brief instruction for the fix, or empty\n\n"
        "If the document looks correct, respond with: PASS\n"
        "Only output ISSUE lines or PASS, nothing else."
    )


def _parse_inspection_response(text: str) -> list[QAIssue]:
    issues = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line == "PASS":
            continue
        if line.startswith("ISSUE|"):
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    issues.append(QAIssue(
                        severity=parts[1].strip().lower(),
                        slide_or_page=int(parts[2].strip()) - 1,
                        description=parts[3].strip(),
                        auto_fixable=len(parts) > 4 and parts[4].strip().lower() == "yes",
                        fix_hint=parts[5].strip() if len(parts) > 5 else "",
                    ))
                except (ValueError, IndexError):
                    pass
    return issues
