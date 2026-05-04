"""LangGraph node — document generation (PPTX/DOCX/XLSX)."""

from __future__ import annotations

import structlog

from config.settings import get_settings
from src.models.state import AgentState

logger = structlog.get_logger()


async def doc_generation_node(state: AgentState) -> dict:
    """
    Generate a document (PPTX/DOCX/XLSX) from the user's request and RAG context.

    Flow:
    1. Run retrieval to get context (already in state from retrieval node, or do it here)
    2. Build document spec from user request + context
    3. Generate file
    4. Return download link + answer text

    Updates:
    - answer: Text description of the generated document
    - generated_file: {filename, download_url, mime_type}
    - citations: From the retrieval used to inform the document
    """
    settings = get_settings()
    doc_output = state.get("document_output", {})
    doc_type = doc_output.get("doc_type", "pptx")
    query = state.get("original_query", "")

    # Get context from retrieval (may need to run it first)
    context = state.get("reranked_results") or state.get("raw_results", [])

    if not context:
        # Run retrieval if not already done
        from src.graph.nodes.retrieval import retrieval_node
        retrieval_state = await retrieval_node(state)
        context = retrieval_state.get("reranked_results") or retrieval_state.get("raw_results", [])

    try:
        from src.document_generation.generator_factory import generate_document

        result = await generate_document(
            doc_type=doc_type,
            user_request=query,
            context_chunks=context,
        )

        download_url = f"/api/v1/files/generated/{result.filename}"

        answer = (
            f"I've generated a {doc_type.upper()} document: **{result.filename}**\n\n"
            f"The document contains information based on your request and "
            f"relevant company documents. You can download it using the link below."
        )

        logger.info(
            "node.doc_generation",
            doc_type=doc_type,
            filename=result.filename,
        )

        return {
            "answer": answer,
            "generated_file": {
                "filename": result.filename,
                "download_url": download_url,
                "mime_type": result.mime_type,
            },
            "citations": state.get("citations", []),
        }

    except Exception as e:
        logger.error("node.doc_generation.failed", error=str(e))
        return {
            "answer": f"I encountered an error generating the {doc_type.upper()} document: {e}",
        }
