"""Factory for creating department-specific LangGraph subgraphs."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from langchain_core.messages import SystemMessage
from langgraph.graph import END, StateGraph

from config.settings import get_settings
from src.agents.prompts.system_prompts import DEPARTMENT_PROMPTS
from src.models.state import AgentState
from src.skills._loader import SkillLoader

logger = structlog.get_logger()

_skill_loader: SkillLoader | None = None


def get_skill_loader() -> SkillLoader:
    global _skill_loader
    if _skill_loader is None:
        settings = get_settings()
        _skill_loader = SkillLoader(settings.SKILLS_DIR)
    return _skill_loader


@dataclass
class DepartmentConfig:
    name: str
    temperature: float
    max_context_chunks: int
    system_prompt: str


DEPARTMENT_CONFIGS: dict[str, DepartmentConfig] = {
    "hr": DepartmentConfig(
        name="hr",
        temperature=0.1,
        max_context_chunks=6,
        system_prompt=DEPARTMENT_PROMPTS["hr"],
    ),
    "finance": DepartmentConfig(
        name="finance",
        temperature=0.0,
        max_context_chunks=8,
        system_prompt=DEPARTMENT_PROMPTS["finance"],
    ),
    "sales": DepartmentConfig(
        name="sales",
        temperature=0.3,
        max_context_chunks=6,
        system_prompt=DEPARTMENT_PROMPTS["sales"],
    ),
    "marketing": DepartmentConfig(
        name="marketing",
        temperature=0.4,
        max_context_chunks=6,
        system_prompt=DEPARTMENT_PROMPTS["marketing"],
    ),
    "general": DepartmentConfig(
        name="general",
        temperature=0.2,
        max_context_chunks=5,
        system_prompt=DEPARTMENT_PROMPTS["general"],
    ),
}


async def build_agent_system_prompt(
    department: str,
    config: DepartmentConfig,
    state: AgentState,
) -> str:
    """
    Build department agent system prompt with progressive skill injection.

    Stage 1: Always include skill metadata (~100 tokens each skill)
    Stage 2: Inject full skill instructions if document generation is needed
    """
    loader = get_skill_loader()
    prompt_parts = [config.system_prompt]

    # Stage 1: Include metadata for all skills
    try:
        all_skills = loader.get_all_metadata()
        if all_skills:
            skill_index = "\n".join(
                f"- **{s.name}**: {s.description}" for s in all_skills
            )
            prompt_parts.append(f"\n## Available Document Skills\n{skill_index}")
    except Exception as e:
        logger.warn("department_factory.skill_metadata_failed", error=str(e))

    # Stage 2: Inject full skill if doc generation needed
    if state.get("requires_document_generation"):
        doc_type = (state.get("document_output") or {}).get("doc_type")
        if doc_type:
            try:
                skill = loader.activate_skill(doc_type)
                prompt_parts.append(
                    f"\n## Active Skill: {skill.meta.name}\n{skill.instructions}"
                )
            except Exception as e:
                logger.warn(
                    "department_factory.skill_inject_failed",
                    doc_type=doc_type,
                    error=str(e),
                )

    return "\n\n".join(prompt_parts)


def _make_department_node(department: str):
    """Create a department-specific reasoning node closure."""
    config = DEPARTMENT_CONFIGS.get(department, DEPARTMENT_CONFIGS["general"])

    async def department_node(state: AgentState) -> dict:
        settings = get_settings()
        query = state.get("original_query", "")
        reranked = state.get("reranked_results") or state.get("raw_results") or []
        graph_context = state.get("graph_context") or []

        # Build context (limited to department's max_context_chunks)
        context_chunks = reranked[: config.max_context_chunks]
        context_parts = []
        for i, chunk in enumerate(context_chunks):
            content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
            title = chunk.get("document_title") if isinstance(chunk, dict) else getattr(chunk, "document_title", "")
            url = chunk.get("sharepoint_url") if isinstance(chunk, dict) else getattr(chunk, "sharepoint_url", "")
            source = f"[{title}]({url})" if url else title
            context_parts.append(f"Source {i + 1}: {source}\n{content[:600]}")

        for ctx in graph_context[:2]:
            title = ctx.get("title", "")
            summary = ctx.get("summary", "")
            if title and summary:
                context_parts.append(f"Related: {title}\n{summary[:300]}")

        context_text = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant documents found."

        system_prompt = await build_agent_system_prompt(department, config, state)

        # Check if we need to build a doc spec
        doc_output = state.get("document_output") or {}
        doc_spec_instruction = ""
        if state.get("requires_document_generation") and doc_output.get("doc_type"):
            doc_type = doc_output["doc_type"]
            doc_spec_instruction = f"""
After your analysis, output a JSON document specification wrapped in <doc_spec> tags:
<doc_spec>
{{
  "title": "document title",
  "sections": [...],
  "data": {{...}},
  "charts": [...]
}}
</doc_spec>
This spec will be used to generate the {doc_type.upper()} file."""

        user_message = f"""Question: {query}

Context:
{context_text}
{doc_spec_instruction}

Provide a thorough answer based on the context above."""

        try:
            answer = await _call_department_llm(
                system_prompt, user_message, config, settings
            )
        except Exception as e:
            logger.error(
                "department_node.llm_failed",
                department=department,
                error=str(e),
            )
            answer = f"I encountered an error processing your {department} query. Please try again."

        # Extract doc spec if present
        updates: dict = {"current_agent": department, "answer": answer}

        if state.get("requires_document_generation"):
            doc_spec = _extract_doc_spec(answer)
            if doc_spec:
                updates["document_output"] = {
                    **doc_output,
                    "spec": doc_spec,
                }

        logger.info(
            "department_node.complete",
            department=department,
            answer_length=len(answer),
        )
        return updates

    department_node.__name__ = f"{department}_agent_node"
    return department_node


async def _call_department_llm(
    system_prompt: str,
    user_message: str,
    config: DepartmentConfig,
    settings,
) -> str:
    """Call LLM with department-specific temperature."""
    if settings.LLM_PROVIDER.value == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            max_completion_tokens=2048,
            temperature=config.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    else:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        response = client.chat.completions.create(
            model=settings.LLM_EXPENSIVE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=2048,
        )
        return response.choices[0].message.content or ""


def _extract_doc_spec(text: str) -> dict | None:
    """Extract JSON doc spec from <doc_spec> tags in LLM output."""
    import json
    import re
    match = re.search(r"<doc_spec>(.*?)</doc_spec>", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None


# Pre-built node functions for each department
hr_agent_node = _make_department_node("hr")
finance_agent_node = _make_department_node("finance")
sales_agent_node = _make_department_node("sales")
marketing_agent_node = _make_department_node("marketing")
general_agent_node = _make_department_node("general")
