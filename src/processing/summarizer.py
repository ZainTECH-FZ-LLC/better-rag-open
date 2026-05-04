"""LLM document summarizer — single-pass and hierarchical map-reduce."""

from __future__ import annotations

import asyncio

import structlog
import tiktoken

from config.settings import get_settings

logger = structlog.get_logger()

# Threshold for hierarchical summarization
SINGLE_PASS_TOKEN_LIMIT = 8000


class LLMSummarizer:
    """
    Document summarization using gpt-4o-mini for cost efficiency.

    Strategy:
    - Documents < 8K tokens: single-pass summarization
    - Larger documents: hierarchical map-reduce (chunk → summarize → combine)

    The AzureChatOpenAI client is instantiated once and reused across calls.
    """

    _llm = None  # shared across all instances

    def __init__(self) -> None:
        self.settings = get_settings()
        self._encoding = tiktoken.encoding_for_model("gpt-4o-mini")

    def _get_llm(self):
        if LLMSummarizer._llm is None:
            from langchain_openai import AzureChatOpenAI
            # Use vision model (gpt-4.1-mini) — much faster than reasoning models
            # for summarization tasks and avoids reasoning token overhead.
            LLMSummarizer._llm = AzureChatOpenAI(
                azure_endpoint=self.settings.VISION_AZURE_ENDPOINT,
                api_key=self.settings.VISION_AZURE_API_KEY,
                api_version=self.settings.VISION_AZURE_API_VERSION,
                azure_deployment=self.settings.VISION_AZURE_DEPLOYMENT,
                max_tokens=8192,
            )
        return LLMSummarizer._llm

    async def summarize(self, text: str, title: str = "") -> str:
        """
        Generate a 200-500 token summary of the document.

        The summary is stored as a separate "summary chunk" and also
        prepended to each chunk's embedding input for better retrieval.
        """
        token_count = len(self._encoding.encode(text))

        if token_count <= SINGLE_PASS_TOKEN_LIMIT:
            return await self._single_pass(text, title)
        else:
            return await self._hierarchical(text, title)

    async def summarize_sheets(
        self, sheets: list[dict], title: str = ""
    ) -> dict[str, str]:
        """
        Generate a 2-3 sentence summary for each sheet in an XLSX file.

        Raw tabular data embeds poorly because numbers carry no semantic weight.
        These summaries are injected into each row-batch chunk's content_with_context
        so that embeddings and reranking scores reflect what the data means, not
        just the raw values.

        All sheets are summarized concurrently.

        Args:
            sheets: List of sheet dicts with keys: name, headers, rows.
            title: Parent document filename for logging.

        Returns:
            {sheet_name: summary_text} — sheets that fail or have no data are omitted.
        """
        async def _summarize_one(sheet: dict) -> tuple[str, str]:
            name = sheet.get("name", "Sheet")
            headers = sheet.get("headers", [])
            rows = sheet.get("rows", [])

            data_rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
            if not headers and not data_rows:
                return name, ""

            lines = [f"Tab: {name}"]
            if headers:
                lines.append("Columns: " + " | ".join(str(h) for h in headers))
            if data_rows:
                sample_count = min(len(data_rows), 20)
                lines.append(f"Sample data ({sample_count} of {len(data_rows)} rows):")
                for row in data_rows[:20]:
                    lines.append(" | ".join(str(c) for c in row if str(c).strip()))

            prompt = f"""Describe what this spreadsheet tab contains in 2-3 sentences. Include the type of data, column names, and any notable totals or patterns visible.

{chr(10).join(lines)}

Description:"""

            try:
                response = await self._get_llm().ainvoke(prompt)
                summary = response.content.strip()
                logger.debug("summarizer.sheet_done", sheet=name, doc=title)
                return name, summary
            except Exception as e:
                logger.warn("summarizer.sheet_failed", sheet=name, error=str(e))
                return name, ""

        results = await asyncio.gather(*(_summarize_one(s) for s in sheets))
        return {name: summary for name, summary in results if summary}

    async def _single_pass(self, text: str, title: str) -> str:
        """Summarize a document that fits within context."""
        llm = self._get_llm()

        prompt = f"""Summarize the following document concisely in 200-500 words.
Focus on key facts, decisions, metrics, and actionable information.
Include any important names, dates, and numbers.

Document Title: {title}

Document Content:
{text[:30000]}

Summary:"""

        response = await llm.ainvoke(prompt)
        summary = response.content.strip()

        logger.info("summarizer.single_pass", title=title, summary_length=len(summary))
        return summary

    async def _hierarchical(self, text: str, title: str) -> str:
        """Hierarchical map-reduce for long documents."""
        llm = self._get_llm()

        # Split into ~4000 token chunks
        chunk_size = 4000
        tokens = self._encoding.encode(text)

        async def _summarize_section(chunk_text: str) -> str:
            prompt = f"""Summarize this section of a document in 2-3 sentences.
Focus on key facts and information.

Section:
{chunk_text}

Section summary:"""
            response = await llm.ainvoke(prompt)
            return response.content.strip()

        # Summarize all sections concurrently
        section_texts = [
            self._encoding.decode(tokens[i : i + chunk_size])
            for i in range(0, len(tokens), chunk_size)
        ]
        chunk_summaries = await asyncio.gather(
            *(_summarize_section(t) for t in section_texts)
        )

        # Combine section summaries
        combined = "\n\n".join(
            f"Section {i + 1}: {s}" for i, s in enumerate(chunk_summaries)
        )

        prompt = f"""Combine these section summaries into a single coherent summary
of 200-500 words. Focus on key facts, decisions, and actionable information.

Document Title: {title}

Section Summaries:
{combined}

Combined Summary:"""

        response = await llm.ainvoke(prompt)
        summary = response.content.strip()

        logger.info(
            "summarizer.hierarchical",
            title=title,
            sections=len(chunk_summaries),
            summary_length=len(summary),
        )
        return summary
