"""Semantic boundary detector — topic-shift detection via sentence embedding similarity."""

from __future__ import annotations

import numpy as np
import structlog

from config.settings import get_settings

logger = structlog.get_logger()

# Cosine similarity drop threshold to mark a topic shift
_DEFAULT_THRESHOLD = 0.75
# Minimum sentences per segment before a split is allowed
_MIN_SENTENCES = 3


class SemanticBoundaryDetector:
    """
    Detects natural topic boundaries in text using sentence-level embedding similarity.

    Used as an optional post-processor on text chunks that exceed the target token
    count to find the best split point within the text.

    Algorithm:
    1. Sentence tokenize the text
    2. Embed each sentence with a lightweight local model
    3. Compute cosine similarity between consecutive sentence embeddings
    4. Identify "semantic drops" (similarity below threshold) as split points
    5. Return candidate split positions for the chunker to use
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self.settings = get_settings()
        self.threshold = threshold
        self._model_name = model_name
        self._model = None  # Lazy loaded

    def _load_model(self):
        """Lazy-load the sentence transformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                logger.info("boundary_detector.model_loaded", model=self._model_name)
            except ImportError:
                logger.warn(
                    "boundary_detector.sentence_transformers_not_installed",
                    hint="pip install sentence-transformers",
                )
                self._model = None
        return self._model

    def find_boundaries(self, text: str) -> list[int]:
        """
        Find sentence indices where topic shifts occur.

        Args:
            text: Input text to analyze.

        Returns:
            List of sentence indices (0-based) that are good split points.
            An empty list means no significant topic shifts were detected.
        """
        if not self.settings.CHUNK_ENABLE_SEMANTIC_BOUNDARY:
            return []

        model = self._load_model()
        if model is None:
            return []

        sentences = self._tokenize_sentences(text)
        if len(sentences) < _MIN_SENTENCES * 2:
            return []

        try:
            embeddings = model.encode(sentences, show_progress_bar=False)
        except Exception as e:
            logger.warn("boundary_detector.encode_failed", error=str(e))
            return []

        similarities = []
        for i in range(len(embeddings) - 1):
            sim = float(np.dot(embeddings[i], embeddings[i + 1]) / (
                np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i + 1]) + 1e-10
            ))
            similarities.append(sim)

        # Find positions where similarity drops below threshold
        boundary_indices = []
        for i, sim in enumerate(similarities):
            if sim < self.threshold and i >= _MIN_SENTENCES:
                boundary_indices.append(i + 1)  # Split after this sentence

        logger.debug(
            "boundary_detector.boundaries_found",
            sentence_count=len(sentences),
            boundary_count=len(boundary_indices),
        )
        return boundary_indices

    def split_at_boundaries(self, text: str) -> list[str]:
        """
        Split text at detected semantic boundaries.

        Args:
            text: Input text.

        Returns:
            List of text segments split at topic boundaries.
            If no boundaries detected, returns [text].
        """
        boundaries = self.find_boundaries(text)
        if not boundaries:
            return [text]

        sentences = self._tokenize_sentences(text)
        segments: list[str] = []
        start = 0

        for boundary in boundaries:
            segment_sentences = sentences[start:boundary]
            if segment_sentences:
                segments.append(" ".join(segment_sentences))
            start = boundary

        # Final segment
        if start < len(sentences):
            segments.append(" ".join(sentences[start:]))

        return [s for s in segments if s.strip()]

    @staticmethod
    def _tokenize_sentences(text: str) -> list[str]:
        """
        Simple sentence tokenizer using common terminal punctuation.
        Falls back to spaCy if available for better accuracy.
        """
        try:
            import spacy
            try:
                nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
                nlp.enable_pipe("senter")
                doc = nlp(text)
                return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
            except Exception:
                pass
        except ImportError:
            pass

        # Fallback: simple regex-based sentence splitting
        import re
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        return [s.strip() for s in sentences if s.strip()]
