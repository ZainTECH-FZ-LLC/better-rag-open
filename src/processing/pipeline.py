"""Document processing pipeline orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

from config.settings import get_settings
from src.processing.metadata import DocumentMetadata, MetadataExtractor
from src.processing.parsers.base import ParsedDocument
from src.processing.parsers.parser_factory import get_parser
from src.processing.summarizer import LLMSummarizer

logger = structlog.get_logger()


@dataclass
class ProcessedDocument:
    """Full output of the processing pipeline."""

    text: str = ""
    markdown: str = ""
    parsed: ParsedDocument | None = None
    metadata: DocumentMetadata | None = None
    summary: str = ""
    section_summaries: dict[str, str] = field(default_factory=dict)
    entities: list[dict] = field(default_factory=list)
    topics: list[dict] = field(default_factory=list)

    # Convenience accessors
    @property
    def department(self) -> str | None:
        return self.metadata.department if self.metadata else None

    @property
    def content_type(self) -> str | None:
        return self.metadata.content_type if self.metadata else None

    @property
    def language(self) -> str:
        return self.metadata.language if self.metadata else "en"


class DocumentProcessingPipeline:
    """
    Orchestrates the full document processing flow:
    1. Parse (file-type specific)
    2. OCR (if needed, e.g., scanned PDFs)
    3. Metadata extraction (Graph API + file properties + LLM)
    4. Summarization
    5. Entity extraction (spaCy NER + LLM for domain entities)
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.metadata_extractor = MetadataExtractor()
        self.summarizer = LLMSummarizer()

    async def process(
        self,
        file_bytes: bytes,
        filename: str,
        file_type: str,
        graph_metadata: dict | None = None,
        extraction_method: str = "hybrid",
    ) -> ProcessedDocument:
        """
        Run the full processing pipeline on a document.

        Args:
            file_bytes: Raw file content.
            filename: Original filename.
            file_type: Extension without dot (pdf, docx, pptx, xlsx).
            graph_metadata: Metadata from Microsoft Graph API.
            extraction_method: OCR/vision method to use (e.g. "vision_llm", "new_ocr").

        Returns:
            ProcessedDocument with text, metadata, summary, entities.
        """
        result = ProcessedDocument()

        # 1. Parse with file-type parser
        parser = get_parser(file_type)
        parsed = await parser.parse(file_bytes, filename)
        result.parsed = parsed
        result.text = parsed.text

        # 2. Vision / OCR extraction — delegated to pluggable extractor
        if file_type in ("pdf", "pptx"):
            try:
                from src.processing.ocr.base_ocr import get_ocr_extractor

                ocr_extractor = get_ocr_extractor(extraction_method)
                result.text = await ocr_extractor.extract(
                    file_bytes, filename, file_type, parsed
                )
                logger.info(
                    "pipeline.ocr_extraction",
                    filename=filename,
                    method=extraction_method,
                    file_type=file_type,
                )
            except NotImplementedError:
                logger.warn(
                    "pipeline.ocr_not_implemented",
                    method=extraction_method,
                    filename=filename,
                )
            except Exception as e:
                logger.warn("pipeline.ocr_extraction_failed", method=extraction_method, error=str(e))
                # Fallback for scanned PDFs: Azure DI OCR
                if file_type == "pdf" and not parsed.file_properties.get("has_text_layer"):
                    logger.info("pipeline.ocr_fallback", filename=filename)
                    if self.settings.OCR_PROVIDER.value == "azure_di":
                        from src.processing.ocr.azure_di import AzureDocumentIntelligenceOCR

                        ocr = AzureDocumentIntelligenceOCR()
                        ocr_result = await ocr.analyze(file_bytes, file_type)
                        result.text = ocr_result.markdown
                        result.markdown = ocr_result.markdown
                        parsed.page_count = ocr_result.page_count
                        parsed.word_count = ocr_result.word_count

        elif file_type == "xlsx" and self.settings.OCR_PROVIDER.value == "azure_di":
            # XLSX: use Azure DI OCR for table/chart extraction augmentation
            try:
                from src.processing.ocr.azure_di import AzureDocumentIntelligenceOCR

                ocr = AzureDocumentIntelligenceOCR()
                ocr_result = await ocr.analyze(file_bytes, file_type)
                result.markdown = ocr_result.markdown
                if ocr_result.tables and not parsed.tables:
                    parsed.tables = ocr_result.tables
            except Exception as e:
                logger.warn("pipeline.ocr_augment_failed", error=str(e))

        # 3-5. Run metadata, summarization, and entity extraction CONCURRENTLY
        #       These are independent LLM/NLP calls — no reason to wait sequentially.
        file_props = {
            "page_count": parsed.page_count,
            "word_count": parsed.word_count,
            "title": parsed.file_properties.get("title"),
        }

        async def _do_metadata():
            return await self.metadata_extractor.extract(
                graph_metadata=graph_metadata,
                file_properties=file_props,
                text_content=result.text,
            )

        async def _do_summarize():
            if result.text and len(result.text) > 100:
                return await self.summarizer.summarize(result.text, title=filename)
            return ""

        async def _do_sheet_summaries():
            if file_type in ("xlsx", "xlsb") and result.parsed and result.parsed.sheets:
                return await self.summarizer.summarize_sheets(
                    result.parsed.sheets, title=filename
                )
            return {}

        async def _do_entities():
            return await self._extract_entities(result.text)

        metadata, summary, section_summaries, entities = await asyncio.gather(
            _do_metadata(), _do_summarize(), _do_sheet_summaries(), _do_entities()
        )

        result.metadata = metadata
        result.summary = summary
        result.section_summaries = section_summaries
        result.entities = entities

        # 6. Topic classification
        result.topics = _extract_topics_from_metadata(result.metadata)

        logger.info(
            "pipeline.completed",
            filename=filename,
            text_length=len(result.text),
            summary_length=len(result.summary),
            entities=len(result.entities),
        )

        return result

    _spacy_nlp = None  # cached across calls

    async def _extract_entities(self, text: str) -> list[dict]:
        """Extract entities using spaCy NER."""
        if not text or len(text) < 50:
            return []

        try:
            import spacy

            if DocumentProcessingPipeline._spacy_nlp is None:
                DocumentProcessingPipeline._spacy_nlp = spacy.load("en_core_web_sm")
            nlp = DocumentProcessingPipeline._spacy_nlp
            # Process a limited text chunk for efficiency
            doc = nlp(text[:50000])

            entities = {}
            for ent in doc.ents:
                if ent.label_ in ("PERSON", "ORG", "GPE", "MONEY", "DATE", "PRODUCT"):
                    key = (ent.text, ent.label_)
                    if key not in entities:
                        entities[key] = {
                            "name": ent.text,
                            "type": ent.label_,
                            "aliases": [],
                            "count": 0,
                            "sections": [],
                            "chunk_ids": [],
                        }
                    entities[key]["count"] += 1

            return list(entities.values())

        except Exception as e:
            logger.warn("pipeline.entity_extraction_failed", error=str(e))
            return []


def _extract_topics_from_metadata(metadata: DocumentMetadata | None) -> list[dict]:
    """Build topic list from metadata."""
    if not metadata or not metadata.topics:
        return []

    return [
        {
            "name": topic,
            "department": metadata.department,
            "relevance": 0.7,
        }
        for topic in metadata.topics
    ]
