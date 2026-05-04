"""Skill loader with progressive disclosure — metadata → full content → references.

Stage 1: Load YAML frontmatter only for all skills (~100 tokens each).
Stage 2: Load full SKILL.md body when a skill is needed.
Stage 3: Load reference/script files on demand.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger()

SKILLS_ROOT = Path(__file__).parent


# ── Data Structures ───────────────────────────────────────────────────────────

class SkillMetadata:
    """Lightweight skill descriptor loaded at startup."""

    def __init__(self, name: str, path: Path, frontmatter: dict[str, Any]) -> None:
        self.name = name
        self.path = path
        self.description: str = frontmatter.get("description", "")
        self.triggers: list[str] = frontmatter.get("triggers", [])
        self.file_types: list[str] = frontmatter.get("file_types", [])
        self.department_hint: list[str] = frontmatter.get("department_hint", [])
        self.version: str = frontmatter.get("version", "1.0.0")
        self.token_estimate: int = frontmatter.get("token_estimate", 0)

    def matches_query(self, query: str, file_type: str | None = None) -> bool:
        """Return True if this skill is relevant to the query."""
        q_lower = query.lower()
        if any(t.lower() in q_lower for t in self.triggers):
            return True
        if file_type and file_type.lower() in self.file_types:
            return True
        return False

    def to_summary(self) -> str:
        """Return a one-line description for supervisor context injection."""
        return f"[{self.name}] {self.description} (triggers: {', '.join(self.triggers[:3])})"


class SkillContent:
    """Full skill content loaded on demand."""

    def __init__(self, metadata: SkillMetadata, body: str) -> None:
        self.metadata = metadata
        self.body = body
        self._references: dict[str, str] = {}
        self._scripts: dict[str, str] = {}

    def get_reference(self, name: str) -> str | None:
        """Load a reference file from the skill's references/ directory."""
        if name in self._references:
            return self._references[name]
        ref_path = self.metadata.path.parent / "references" / name
        if ref_path.exists():
            content = ref_path.read_text(encoding="utf-8")
            self._references[name] = content
            logger.debug("skill.reference_loaded", skill=self.metadata.name, ref=name)
            return content
        return None

    def get_script(self, name: str) -> str | None:
        """Load a script file from the skill's scripts/ directory."""
        if name in self._scripts:
            return self._scripts[name]
        script_path = self.metadata.path.parent / "scripts" / name
        if script_path.exists():
            content = script_path.read_text(encoding="utf-8")
            self._scripts[name] = content
            logger.debug("skill.script_loaded", skill=self.metadata.name, script=name)
            return content
        return None

    def list_references(self) -> list[str]:
        ref_dir = self.metadata.path.parent / "references"
        if ref_dir.exists():
            return [f.name for f in ref_dir.iterdir() if f.is_file()]
        return []

    def list_scripts(self) -> list[str]:
        script_dir = self.metadata.path.parent / "scripts"
        if script_dir.exists():
            return [
                str(f.relative_to(script_dir))
                for f in script_dir.rglob("*.py")
            ]
        return []

    def to_prompt_block(self, include_references: list[str] | None = None) -> str:
        """Build a prompt block ready for injection into a department agent."""
        lines = [f"# SKILL: {self.metadata.name}", "", self.body]
        if include_references:
            for ref_name in include_references:
                ref_content = self.get_reference(ref_name)
                if ref_content:
                    lines += ["", f"## Reference: {ref_name}", ref_content]
        return "\n".join(lines)


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_skill_file(path: Path) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from markdown body."""
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
    if match:
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            frontmatter = {}
        body = match.group(2)
    else:
        frontmatter = {}
        body = raw
    return frontmatter, body


# ── Loader ────────────────────────────────────────────────────────────────────

class SkillLoader:
    """
    Progressive disclosure skill manager.

    Usage:
        loader = SkillLoader()
        # Stage 1 — all metadata loaded eagerly
        for meta in loader.all_metadata():
            print(meta.to_summary())

        # Stage 2 — full skill loaded on demand
        skill = loader.load("pptx")

        # Stage 3 — reference files loaded on demand
        ref = skill.get_reference("pptxgenjs.md")
    """

    def __init__(self, skills_root: Path | None = None) -> None:
        self._root = skills_root or SKILLS_ROOT
        self._metadata: dict[str, SkillMetadata] = {}
        self._content_cache: dict[str, SkillContent] = {}
        self._scan()

    def _scan(self) -> None:
        """Stage 1: scan all SKILL.md files and load frontmatter only."""
        for skill_md in self._root.rglob("SKILL.md"):
            name = skill_md.parent.name
            try:
                frontmatter, _ = _parse_skill_file(skill_md)
                meta = SkillMetadata(name=name, path=skill_md, frontmatter=frontmatter)
                self._metadata[name] = meta
                logger.debug("skill.discovered", name=name)
            except Exception as exc:
                logger.warning("skill.scan_failed", name=name, error=str(exc))

    def all_metadata(self) -> list[SkillMetadata]:
        return list(self._metadata.values())

    def get_metadata(self, name: str) -> SkillMetadata | None:
        return self._metadata.get(name)

    def load(self, name: str) -> SkillContent | None:
        """Stage 2: load full skill body."""
        if name in self._content_cache:
            return self._content_cache[name]
        meta = self._metadata.get(name)
        if meta is None:
            logger.warning("skill.not_found", name=name)
            return None
        try:
            _, body = _parse_skill_file(meta.path)
            content = SkillContent(metadata=meta, body=body)
            self._content_cache[name] = content
            logger.info("skill.loaded", name=name)
            return content
        except Exception as exc:
            logger.error("skill.load_failed", name=name, error=str(exc))
            return None

    def resolve_for_query(
        self,
        query: str,
        document_type: str | None = None,
        department: str | None = None,
    ) -> list[SkillMetadata]:
        """Return skills relevant to this query (metadata only, Stage 1)."""
        matched = []
        for meta in self._metadata.values():
            if meta.matches_query(query, document_type):
                matched.append(meta)
            elif department and department in meta.department_hint:
                matched.append(meta)
        return matched

    def build_supervisor_context(self) -> str:
        """Return a compact skills summary for injection into the supervisor prompt."""
        if not self._metadata:
            return ""
        lines = ["Available document generation skills:"]
        for meta in self._metadata.values():
            lines.append(f"  - {meta.to_summary()}")
        return "\n".join(lines)

    def inject_into_agent(
        self,
        query: str,
        document_type: str | None = None,
        department: str | None = None,
        include_references: list[str] | None = None,
    ) -> str:
        """
        Build the skill prompt block to inject into a department agent system prompt.

        Loads full content for matched skills (Stage 2), returns empty string if none.
        """
        matched = self.resolve_for_query(query, document_type, department)
        if not matched:
            return ""
        blocks = []
        for meta in matched:
            skill = self.load(meta.name)
            if skill:
                blocks.append(skill.to_prompt_block(include_references=include_references))
        return "\n\n---\n\n".join(blocks)


# ── Singleton ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_skill_loader() -> SkillLoader:
    """Return the application-wide skill loader singleton."""
    loader = SkillLoader()
    logger.info("skill_loader.initialized", count=len(loader.all_metadata()))
    return loader
