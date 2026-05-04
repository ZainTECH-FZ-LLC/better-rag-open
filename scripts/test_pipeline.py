"""
Test the actual RetrievalPipeline end-to-end against locally ingested documents.

Exercises the real pipeline code (query analysis → HyDE → vector search →
rerank → graph expansion → graph chunk fetching) with verbose output so you
can see exactly what each stage produces.

Usage:
    python scripts/test_pipeline.py "What is Zain's strategy?"
    python scripts/test_pipeline.py "Compare revenue across subsidiaries" --no-answer
    python scripts/test_pipeline.py   # interactive mode
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Fix Windows terminal encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _print_chunk(i: int, chunk, prefix: str = "") -> None:
    """Pretty-print a single chunk result."""
    if hasattr(chunk, "content"):
        # ChunkResult dataclass
        title = chunk.document_title or "Unknown"
        heading = chunk.section_heading or ""
        pages = chunk.page_numbers or []
        score = chunk.score
        content = chunk.content
        via = ""
    else:
        # dict (from RerankedResult or graph_chunks)
        title = chunk.get("document_title") or "Unknown"
        heading = chunk.get("section_heading") or ""
        pages = chunk.get("page_numbers") or []
        score = chunk.get("score") or chunk.get("rerank_score", 0)
        content = chunk.get("content", "")
        via = "  [graph]" if chunk.get("via_graph") else ""

    label = f"{prefix}{score:.4f}" if isinstance(score, float) else f"{prefix}{score}"
    print(f"  [{i}] {label}  {title}{via}" + (f" — {heading}" if heading else ""))
    if pages:
        print(f"       pages: {pages}")
    print(f"       {content[:200].strip()}...")
    print()


async def run_pipeline(query: str, top_k: int = 10, skip_answer: bool = False) -> None:
    """Run the full RetrievalPipeline and print each stage."""
    from src.retrieval.pipeline import RetrievalPipeline
    from src.storage.db import get_db_session

    print(f"\nQuery: {query}")
    print(f"{'=' * 60}")

    async with get_db_session() as db:
        pipeline = RetrievalPipeline(db_session=db)

        print("\n[1] Running pipeline.retrieve()...")
        result = await pipeline.retrieve(
            query=query,
            user_id="anonymous",
            k=top_k,
            fetch_k=top_k * 4,
        )

    # ── Query Analysis ──
    analysis = result.query_analysis
    print(f"\n--- Query Analysis ---")
    print(f"  query_type:    {analysis.query_type}")
    print(f"  strategy:      {analysis.retrieval_strategy}")
    print(f"  department:    {analysis.target_department}")
    print(f"  reformulated:  {analysis.reformulated_query or '(none)'}")
    print(f"  filters:       {analysis.metadata_filters}")
    print(f"  doc_gen:       {analysis.requires_document_generation}")

    # ── Vector Results ──
    print(f"\n--- Vector Search: {len(result.vector_results)} result(s) ---\n")
    for i, chunk in enumerate(result.vector_results[:top_k], 1):
        _print_chunk(i, chunk, prefix="vec=")

    # ── Reranked Results ──
    if result.reranked_results:
        print(f"--- Reranked: {len(result.reranked_results)} result(s) ---\n")
        for i, rr in enumerate(result.reranked_results, 1):
            title = rr.chunk.document_title or "Unknown"
            heading = rr.chunk.section_heading or ""
            pages = rr.chunk.page_numbers or []
            print(f"  [{i}] rerank={rr.rerank_score:.4f}  vec={rr.chunk.score:.4f}  {title}" + (f" — {heading}" if heading else ""))
            if pages:
                print(f"       pages: {pages}")
            print(f"       {rr.chunk.content[:200].strip()}...")
            print()
    else:
        print("--- Reranking: skipped or failed (using diversified vector order) ---\n")

    # ── Graph Context ──
    if result.graph_context:
        print(f"--- Graph Context: {len(result.graph_context)} related doc(s) ---\n")
        for ctx in result.graph_context:
            rel_types = ctx.get("relationship_types") or []
            graph_score = ctx.get("max_weight") or ctx.get("graph_score") or 0
            print(f"  {ctx.get('title', '?')}  [{', '.join(rel_types)}]  score={float(graph_score):.2f}")
        print()
    else:
        print("--- Graph Context: none ---\n")

    # ── Graph Chunks ──
    if result.graph_chunks:
        print(f"--- Graph Chunks: {len(result.graph_chunks)} chunk(s) from related docs ---\n")
        for i, chunk in enumerate(result.graph_chunks, 1):
            _print_chunk(i, chunk, prefix="vec=")
    else:
        print("--- Graph Chunks: none ---\n")

    # ── All Chunks (what gets sent to LLM) ──
    all_chunks = result.all_chunks
    print(f"--- Total context for LLM: {len(all_chunks)} chunk(s) from {len(result.citations)} doc(s) ---\n")

    # ── Citations ──
    print("Citations:")
    for i, cit in enumerate(result.citations, 1):
        print(f"  [{i}] {cit['document_title']}  (score={cit['score']:.4f})")
    print()

    # ── Answer Generation ──
    if not skip_answer:
        print(f"{'=' * 60}")
        print("Generating answer...\n")
        from scripts.test_query import generate_answer

        # Convert ChunkResult objects to dicts for generate_answer
        chunk_dicts = []
        for chunk in all_chunks:
            if hasattr(chunk, "content"):
                chunk_dicts.append({
                    "content": chunk.content,
                    "content_with_context": chunk.content_with_context,
                    "document_title": chunk.document_title,
                    "section_heading": chunk.section_heading,
                    "page_numbers": chunk.page_numbers,
                    "source": chunk.sharepoint_url,
                })
            else:
                chunk_dicts.append(chunk)

        answer = await generate_answer(query, chunk_dicts, graph_context=result.graph_context)
        print(answer)
        print()


async def interactive_loop(top_k: int, skip_answer: bool) -> None:
    print("\nbetter-rag pipeline tester (exercises actual RetrievalPipeline)")
    print(f"top_k={top_k}  answer={'off' if skip_answer else 'on'}")
    print("Type a question and press Enter. Type 'exit' or Ctrl+C to quit.\n")

    while True:
        try:
            query = input("Query> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "q"):
            break

        await run_pipeline(query, top_k, skip_answer)
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test the actual RetrievalPipeline end-to-end.")
    parser.add_argument("query", nargs="?", help="Single query (omit for interactive loop)")
    parser.add_argument("--top-k", type=int, default=10, help="Number of chunks to retrieve (default: 10)")
    parser.add_argument("--no-answer", action="store_true", help="Show retrieval results only, skip LLM answer")
    args = parser.parse_args()

    if args.query:
        asyncio.run(run_pipeline(args.query, args.top_k, args.no_answer))
    else:
        asyncio.run(interactive_loop(args.top_k, args.no_answer))
