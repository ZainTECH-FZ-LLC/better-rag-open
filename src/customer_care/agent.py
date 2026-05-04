"""Customer Care agent — retrieves from CC KB and generates structured adaptive responses."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import structlog

from config.settings import get_settings
from src.customer_care.prompts import build_cc_system_prompt
from src.customer_care.retrieval import CCRetrievalPipeline

logger = structlog.get_logger()


class CustomerCareAgent:
    """
    Standalone Customer Care agent for internal support agents.

    Uses the full retrieval pipeline (HyDE/cosine/MMR + reranking + graph expansion)
    against the CC knowledge base, then generates an adaptive structured response
    with only the sections relevant to the query.

    Response sections (all optional except answer):
    - answer: always present — concise, factual
    - policy_link: included when a specific policy document is relevant
    - script: included when the agent needs customer-facing language
    - upsell: included when a genuine product opportunity exists
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def stream_answer(
        self,
        question: str,
        user_id: str,
        channel: str = "chat",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Run the full CC retrieval + generation pipeline and yield SSE-style events.

        Yields:
            status events during retrieval and generation
            token event with the answer text
            cc_policy_link event if a policy link is relevant
            cc_script event if a customer-facing script is relevant
            cc_upsell event if an upsell opportunity is identified
        """
        from src.storage.db import get_db_session

        yield {"type": "status", "message": "Searching knowledge base...", "step": "retrieval"}

        async with get_db_session() as db:
            pipeline = CCRetrievalPipeline(db_session=db)
            try:
                result = await pipeline.retrieve(
                    query=question,
                    user_id=user_id,
                    k=8,
                    fetch_k=40,
                )
            except Exception as exc:
                logger.error("cc_agent.retrieval_failed", error=str(exc), user_id=user_id)
                yield {"type": "error", "message": f"Retrieval failed: {exc}"}
                return

        if not result.final_chunks:
            yield {
                "type": "token",
                "content": (
                    "I couldn't find relevant information in the knowledge base for that query. "
                    "Please check that the relevant policies have been ingested, or rephrase the question."
                ),
            }
            return

        yield {"type": "status", "message": "Generating response...", "step": "generation"}

        # Build context block (same pattern as DepartmentAgent.generate_answer)
        context_parts = []
        for i, chunk in enumerate(result.final_chunks[:10]):
            title = chunk.document_title or "Unknown"
            url = chunk.sharepoint_url or ""
            section = chunk.section_heading or ""
            content = chunk.content_with_context or chunk.content

            header = f"[{i + 1}] {title}"
            if section:
                header += f" — {section}"
            if url:
                header += f"\nSource: {url}"

            # Attach policy_url to help the LLM surface it
            if hasattr(chunk, "policy_url") and chunk.policy_url:
                header += f"\nPolicy URL: {chunk.policy_url}"

            context_parts.append(f"{header}\n{content}")

        if result.graph_context:
            related_parts = []
            for doc in result.graph_context[:3]:
                title = doc.get("title", "")
                summary = doc.get("summary", "")
                url = doc.get("sharepoint_url", "")
                related_parts.append(f"- [{title}]({url}): {summary[:200]}")
            if related_parts:
                context_parts.append(
                    "--- Related Documents ---\n" + "\n".join(related_parts)
                )

        context_block = "\n\n---\n\n".join(context_parts)

        # Build system prompt with brand guidelines and upsell config
        system_prompt = build_cc_system_prompt(
            brand_guidelines=self.settings.CC_BRAND_GUIDELINES,
            upsell_products=self.settings.get_cc_upsell_products(),
            channel=channel,
        )

        user_prompt = (
            f"Context:\n{context_block}\n\n"
            f"Customer care agent's question: {question}\n\n"
            f"Return the JSON response object. Include only sections that are genuinely relevant."
        )

        # Call LLM and parse structured response
        try:
            llm = self._get_llm()
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            raw = response.content.strip()

            # Strip markdown fences if the model wrapped the JSON
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rstrip("`").strip()

            data = json.loads(raw)

        except json.JSONDecodeError as exc:
            logger.error("cc_agent.json_parse_failed", error=str(exc), raw=raw[:200])
            yield {"type": "error", "message": "Response parsing failed. Please retry."}
            return
        except Exception as exc:
            logger.error("cc_agent.llm_failed", error=str(exc))
            yield {"type": "error", "message": f"Generation failed: {exc}"}
            return

        logger.info(
            "cc_agent.answered",
            user_id=user_id,
            channel=channel,
            sections=list(data.keys()),
            chunks_used=len(result.final_chunks),
        )

        # Emit answer (always present)
        answer = data.get("answer", "")
        if answer:
            yield {"type": "token", "content": answer}

        # Emit policy link if present
        policy_link = data.get("policy_link")
        if policy_link and isinstance(policy_link, dict):
            yield {
                "type": "cc_policy_link",
                "title": policy_link.get("title", ""),
                "url": policy_link.get("url", ""),
            }

        # Emit script if present
        script = data.get("script")
        if script:
            yield {
                "type": "cc_script",
                "channel": channel,
                "content": script,
            }

        # Emit upsell if present
        upsell = data.get("upsell")
        if upsell and isinstance(upsell, dict):
            yield {
                "type": "cc_upsell",
                "product": upsell.get("product", ""),
                "pitch": upsell.get("pitch", ""),
            }

    def _get_llm(self):
        """Get the LLM for response generation."""
        if self.settings.LLM_PROVIDER.value == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                api_key=self.settings.ANTHROPIC_API_KEY,
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                temperature=0.1,
            )
        else:
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
                api_key=self.settings.AZURE_OPENAI_API_KEY,
                api_version=self.settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=self.settings.LLM_EXPENSIVE_MODEL,
                max_tokens=1500,
            )
