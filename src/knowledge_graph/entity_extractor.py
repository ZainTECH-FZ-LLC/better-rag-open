"""Entity extractor — spaCy NER + LLM for domain-specific entities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from config.settings import get_settings

logger = structlog.get_logger()

# spaCy entity types we capture as standard entities
SPACY_ENTITY_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "DATE", "EVENT", "LAW"}

# LLM prompt for domain-specific entity extraction
_DOMAIN_ENTITY_PROMPT = """Extract domain-specific entities from the following document text.
Focus on entities NOT captured by standard NLP (policy names, project codes, product names,
internal systems, regulatory references, KPIs, custom metrics).

Return a JSON array of objects with fields:
- name: entity name (normalized)
- type: one of POLICY, PROJECT, PRODUCT, METRIC, SYSTEM, REGULATION, OTHER
- aliases: list of alternative names/abbreviations (can be empty)

Text:
{text}

Return ONLY valid JSON. No explanation."""


@dataclass
class ExtractedEntity:
    """A single extracted entity."""

    name: str
    type: str  # PERSON, ORG, POLICY, PROJECT, PRODUCT, METRIC, etc.
    aliases: list[str] = field(default_factory=list)
    count: int = 1
    sections: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)


class EntityExtractor:
    """
    Extracts entities from document text using two complementary approaches:

    1. spaCy NER (zero cost) — PERSON, ORG, GPE, PRODUCT, MONEY, DATE, LAW
    2. LLM extraction (cheap model) — domain-specific: POLICY, PROJECT, METRIC, SYSTEM

    Results are merged, deduplicated, and normalized before being stored in Neo4j.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._nlp = None  # Lazy loaded
        self._llm_client = None  # Lazy loaded

    def _load_spacy(self):
        """Lazy-load spaCy model."""
        if self._nlp is None:
            try:
                import spacy
                self._nlp = spacy.load("en_core_web_sm")
                logger.info("entity_extractor.spacy_loaded")
            except (ImportError, OSError) as e:
                logger.warn("entity_extractor.spacy_unavailable", error=str(e))
                self._nlp = None
        return self._nlp

    def _get_llm_client(self):
        """Lazy-load LLM client for domain entity extraction."""
        if self._llm_client is None:
            try:
                if self.settings.LLM_PROVIDER.value == "anthropic":
                    import anthropic
                    self._llm_client = anthropic.Anthropic(
                        api_key=self.settings.ANTHROPIC_API_KEY
                    )
                else:
                    from openai import AzureOpenAI
                    self._llm_client = AzureOpenAI(
                        azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
                        api_key=self.settings.AZURE_OPENAI_API_KEY,
                        api_version=self.settings.AZURE_OPENAI_API_VERSION,
                    )
            except Exception as e:
                logger.warn("entity_extractor.llm_unavailable", error=str(e))
        return self._llm_client

    async def extract(
        self,
        text: str,
        chunk_id: str | None = None,
        section_heading: str | None = None,
        use_llm: bool = True,
    ) -> list[ExtractedEntity]:
        """
        Extract entities from text.

        Args:
            text: Document text or chunk content.
            chunk_id: Optional chunk ID to associate entities with.
            section_heading: Section context for the entity.
            use_llm: Whether to run the LLM domain-entity pass (can be disabled for speed).

        Returns:
            Deduplicated list of ExtractedEntity objects.
        """
        entities: dict[str, ExtractedEntity] = {}

        # 1. spaCy NER (free, fast)
        spacy_entities = self._extract_spacy(text, chunk_id, section_heading)
        for ent in spacy_entities:
            key = f"{ent.name.lower()}:{ent.type}"
            if key in entities:
                entities[key].count += ent.count
                if chunk_id and chunk_id not in entities[key].chunk_ids:
                    entities[key].chunk_ids.append(chunk_id)
                if section_heading and section_heading not in entities[key].sections:
                    entities[key].sections.append(section_heading)
            else:
                entities[key] = ent

        # 2. LLM domain-specific entities (cheap model)
        if use_llm and len(text) > 100:
            try:
                llm_entities = await self._extract_llm(text, chunk_id, section_heading)
                for ent in llm_entities:
                    key = f"{ent.name.lower()}:{ent.type}"
                    if key not in entities:
                        entities[key] = ent
                    else:
                        # Merge aliases
                        existing = entities[key]
                        for alias in ent.aliases:
                            if alias not in existing.aliases:
                                existing.aliases.append(alias)
            except Exception as e:
                logger.warn("entity_extractor.llm_failed", error=str(e))

        result = list(entities.values())
        logger.debug(
            "entity_extractor.complete",
            entity_count=len(result),
            chunk_id=chunk_id,
        )
        return result

    async def extract_from_chunks(
        self,
        chunks: list[dict],
        use_llm: bool = True,
    ) -> list[ExtractedEntity]:
        """
        Extract and aggregate entities across all chunks of a document.

        Args:
            chunks: List of chunk dicts with chunk_id, content, section_heading.
            use_llm: Whether to use LLM for domain-specific entities.

        Returns:
            Merged entity list with counts and chunk/section references.
        """
        all_entities: dict[str, ExtractedEntity] = {}

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", "")
            content = chunk.get("content", "")
            section = chunk.get("section_heading")

            chunk_entities = await self.extract(
                text=content,
                chunk_id=chunk_id,
                section_heading=section,
                use_llm=use_llm,
            )

            for ent in chunk_entities:
                key = f"{ent.name.lower()}:{ent.type}"
                if key in all_entities:
                    existing = all_entities[key]
                    existing.count += ent.count
                    for cid in ent.chunk_ids:
                        if cid not in existing.chunk_ids:
                            existing.chunk_ids.append(cid)
                    for sec in ent.sections:
                        if sec not in existing.sections:
                            existing.sections.append(sec)
                    for alias in ent.aliases:
                        if alias not in existing.aliases:
                            existing.aliases.append(alias)
                else:
                    all_entities[key] = ent

        # Filter out low-count noise (entities appearing only once are often errors)
        filtered = [e for e in all_entities.values() if e.count >= 1]
        logger.info("entity_extractor.document_complete", entity_count=len(filtered))
        return filtered

    def _extract_spacy(
        self,
        text: str,
        chunk_id: str | None,
        section_heading: str | None,
    ) -> list[ExtractedEntity]:
        """Run spaCy NER on text."""
        nlp = self._load_spacy()
        if nlp is None:
            return []

        # Truncate to spaCy's max length
        max_chars = 100_000
        if len(text) > max_chars:
            text = text[:max_chars]

        try:
            doc = nlp(text)
        except Exception as e:
            logger.warn("entity_extractor.spacy_failed", error=str(e))
            return []

        entity_counts: dict[tuple[str, str], int] = {}
        for ent in doc.ents:
            if ent.label_ in SPACY_ENTITY_TYPES:
                normalized = _normalize_name(ent.text)
                if len(normalized) < 2 or len(normalized) > 100:
                    continue
                key = (normalized, ent.label_)
                entity_counts[key] = entity_counts.get(key, 0) + 1

        entities = []
        for (name, etype), count in entity_counts.items():
            entities.append(ExtractedEntity(
                name=name,
                type=etype,
                count=count,
                sections=[section_heading] if section_heading else [],
                chunk_ids=[chunk_id] if chunk_id else [],
            ))

        return entities

    async def _extract_llm(
        self,
        text: str,
        chunk_id: str | None,
        section_heading: str | None,
    ) -> list[ExtractedEntity]:
        """Run cheap LLM to extract domain-specific entities."""
        client = self._get_llm_client()
        if client is None:
            return []

        # Truncate text to avoid excessive LLM cost
        max_chars = 3000
        truncated = text[:max_chars] + ("..." if len(text) > max_chars else "")
        prompt = _DOMAIN_ENTITY_PROMPT.format(text=truncated)

        try:
            if self.settings.LLM_PROVIDER.value == "anthropic":
                import anthropic
                response = client.messages.create(
                    model=self.settings.LLM_CHEAP_MODEL,
                    max_completion_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = response.content[0].text
            else:
                response = client.chat.completions.create(
                    model=self.settings.LLM_CHEAP_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=512,
                )
                raw = response.choices[0].message.content or ""
        except Exception as e:
            logger.warn("entity_extractor.llm_call_failed", error=str(e))
            return []

        # Parse JSON response
        try:
            # Strip markdown fences if present
            raw = re.sub(r"```(?:json)?", "", raw).strip()
            items = json.loads(raw)
            if not isinstance(items, list):
                return []
        except (json.JSONDecodeError, ValueError) as e:
            logger.warn("entity_extractor.llm_parse_failed", error=str(e), raw=raw[:200])
            return []

        entities = []
        for item in items:
            name = _normalize_name(item.get("name", ""))
            etype = item.get("type", "OTHER").upper()
            aliases = [_normalize_name(a) for a in item.get("aliases", []) if a]
            if name and len(name) >= 2:
                entities.append(ExtractedEntity(
                    name=name,
                    type=etype,
                    aliases=aliases,
                    count=1,
                    sections=[section_heading] if section_heading else [],
                    chunk_ids=[chunk_id] if chunk_id else [],
                ))

        return entities


def _normalize_name(name: str) -> str:
    """Normalize an entity name: strip whitespace, collapse internal spaces."""
    return re.sub(r"\s+", " ", name.strip())


async def extract_topics(
    text: str,
    department: str | None = None,
    max_topics: int = 5,
) -> list[dict]:
    """
    Classify a document into topics via cheap LLM.

    Args:
        text: Document text (summary or first N tokens).
        department: Department context for topic classification.
        max_topics: Maximum number of topics to return.

    Returns:
        List of topic dicts with name, department, relevance (0-1).
    """
    settings = get_settings()
    prompt = f"""Identify the main topics in this document text.
Return up to {max_topics} topics as a JSON array with fields:
- name: topic name (concise, 2-5 words)
- relevance: relevance score 0.0-1.0
{f'Consider this is a {department} department document.' if department else ''}

Text:
{text[:3000]}

Return ONLY valid JSON array. No explanation."""

    try:
        if settings.LLM_PROVIDER.value == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.LLM_CHEAP_MODEL,
                max_completion_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
        else:
            from openai import AzureOpenAI
            client = AzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            response = client.chat.completions.create(
                model=settings.LLM_CHEAP_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=256,
            )
            raw = response.choices[0].message.content or ""

        raw = re.sub(r"```(?:json)?", "", raw).strip()
        topics = json.loads(raw)
        if not isinstance(topics, list):
            return []

        result = []
        for t in topics[:max_topics]:
            name = t.get("name", "").strip()
            if name:
                result.append({
                    "name": name,
                    "department": department,
                    "relevance": float(t.get("relevance", 0.5)),
                })
        return result

    except Exception as e:
        logger.warn("topic_extractor.failed", error=str(e))
        return []
