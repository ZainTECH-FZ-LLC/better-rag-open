"""Department sub-agent — specialized LLM agent with department-specific system prompt."""

from __future__ import annotations

from typing import Any

import structlog

from config.settings import get_settings
from src.agents.prompts.system_prompts import DEPARTMENT_PROMPTS

logger = structlog.get_logger()


class DepartmentAgent:
    """
    A department-specific sub-agent that generates answers using
    a specialized system prompt and department-filtered context.

    Each sub-agent:
    - Has a department-specific system prompt with domain expertise
    - Receives only context relevant to its department
    - Formats answers with department-specific citation patterns
    """

    def __init__(self, department: str) -> None:
        self.department = department
        self.settings = get_settings()
        self.system_prompt = DEPARTMENT_PROMPTS.get(department, DEPARTMENT_PROMPTS["general"])

    async def generate_answer(
        self,
        query: str,
        context_chunks: list[dict[str, Any]],
        graph_context: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Generate a department-specific answer.

        Args:
            query: The user's original query.
            context_chunks: Retrieved chunks filtered/ranked for this department.
            graph_context: Related documents from graph expansion.

        Returns:
            Formatted answer with citations.
        """
        # Build context block
        context_parts = []
        for i, chunk in enumerate(context_chunks[:10]):
            title = chunk.get("document_title", "Unknown")
            url = chunk.get("sharepoint_url", "")
            section = chunk.get("section_heading", "")
            content = chunk.get("content_with_context") or chunk.get("content", "")

            header = f"[{i + 1}] {title}"
            if section:
                header += f" — {section}"
            if url:
                header += f"\nSource: {url}"

            context_parts.append(f"{header}\n{content}")

        if graph_context:
            related_parts = []
            for doc in graph_context[:3]:
                title = doc.get("title", "")
                summary = doc.get("summary", "")
                url = doc.get("sharepoint_url", "")
                related_parts.append(f"- [{title}]({url}): {summary[:200]}")

            if related_parts:
                context_parts.append(
                    "--- Related Documents ---\n" + "\n".join(related_parts)
                )

        context_block = "\n\n---\n\n".join(context_parts)

        user_prompt = f"""Context:
{context_block}

Question: {query}

Provide a thorough answer based on the context above. Cite sources with [Title](URL) format."""

        # Call LLM
        llm = self._get_llm()
        from langchain_core.messages import HumanMessage, SystemMessage

        response = await llm.ainvoke([
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_prompt),
        ])

        logger.info(
            "department_agent.answer",
            department=self.department,
            query_length=len(query),
            context_chunks=len(context_chunks),
            answer_length=len(response.content),
        )

        return response.content

    def _get_llm(self):
        """Get the LLM for this agent (uses expensive model)."""
        if self.settings.LLM_PROVIDER.value == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                api_key=self.settings.ANTHROPIC_API_KEY,
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                temperature=0.2,
            )
        else:
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_endpoint=self.settings.AZURE_OPENAI_ENDPOINT,
                api_key=self.settings.AZURE_OPENAI_API_KEY,
                api_version=self.settings.AZURE_OPENAI_API_VERSION,
                azure_deployment=self.settings.LLM_EXPENSIVE_MODEL,
                max_tokens=2000,
            )


def get_department_agent(department: str | None) -> DepartmentAgent:
    """Factory function to get a department agent."""
    dept = department or "general"
    if dept not in DEPARTMENT_PROMPTS:
        dept = "general"
    return DepartmentAgent(dept)
