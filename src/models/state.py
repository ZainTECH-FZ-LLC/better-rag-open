"""LangGraph AgentState TypedDict definitions."""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import BaseMessage


class UserContext(TypedDict):
    user_id: str
    user_email: str
    department: str | None
    roles: list[str]
    access_level: str | None


class DocumentOutput(TypedDict, total=False):
    doc_type: str  # pptx, docx, xlsx
    template_name: str | None
    spec: dict[str, Any]


class AgentState(TypedDict, total=False):
    # Conversation
    messages: list[BaseMessage]
    chat_history: str | None  # recent turn summary for follow-up resolution
    user_context: UserContext
    original_query: str
    reformulated_query: str | None

    # Query analysis
    query_type: str  # factual, analytical, procedural, generative
    target_department: str | None
    requires_document_generation: bool
    document_output: DocumentOutput | None

    # Retrieval control
    metadata_filters: dict[str, Any]
    retrieval_strategy: str  # cosine, hyde_cosine, mmr

    # Results
    raw_results: list[dict[str, Any]]
    reranked_results: list[dict[str, Any]]
    graph_context: list[dict[str, Any]]
    graph_chunks: list[dict[str, Any]]

    # Output
    answer: str
    answer_tokens: list[str]
    citations: list[dict[str, Any]]
    generated_file: dict[str, Any] | None

    # Control flow
    current_agent: str | None
    iteration_count: int
    should_retry_retrieval: bool
    is_smalltalk: bool
