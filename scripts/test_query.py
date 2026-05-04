"""
Test RAG queries against locally ingested documents.

Bypasses RBAC (no SharePoint permissions required) — mirrors local_ingest.py approach.

Usage:
    python scripts/test_query.py "What is Zain's strategy for 2023?"
    python scripts/test_query.py "Compare Zain Kuwait and Zain Jordan revenue" --top-k 8
    python scripts/test_query.py "What are the key financial targets?" --no-answer
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Fix Windows terminal encoding for non-ASCII LLM output
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def search_chunks(query: str, fetch_k: int = 20, use_hyde: bool = False) -> list[dict]:
    """Direct cosine similarity search — no RBAC join (local test mode).

    Fetches fetch_k candidates for downstream reranking.
    If use_hyde=True, generates a hypothetical answer first and embeds that
    instead of the raw query (improves recall for terse/keyword queries).
    """
    from sqlalchemy import text

    from src.embedding.azure_openai import AzureOpenAIEmbedder
    from src.storage.db import get_db_session

    embedder = AzureOpenAIEmbedder()
    if use_hyde:
        from openai import AzureOpenAI
        from config.settings import get_settings as _gs
        _s = _gs()
        print("  [HyDE] Generating hypothetical document...")
        _client = AzureOpenAI(
            azure_endpoint=_s.AZURE_OPENAI_ENDPOINT,
            api_key=_s.AZURE_OPENAI_API_KEY,
            api_version=_s.AZURE_OPENAI_API_VERSION,
        )
        _resp = _client.chat.completions.create(
            model=_s.LLM_EXPENSIVE_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that writes document excerpts."},
                {"role": "user", "content": (
                    f"Write a short passage (150-250 words) from an internal company document "
                    f"that would directly answer the following question. Write it as if it were "
                    f"an excerpt from an actual financial report or investment analysis. "
                    f"Include specific numbers, ranges, and professional terminology.\n\n"
                    f"Question: {query}\n\nDocument excerpt:"
                )},
            ],
            max_completion_tokens=400,
        )
        hypo_doc = _resp.choices[0].message.content or query
        print(f"  [HyDE] ({len(hypo_doc)} chars): {hypo_doc[:200].strip()}...")
        query_embedding = await embedder.embed_query(hypo_doc)
    else:
        query_embedding = await embedder.embed_query(query)
    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

    async with get_db_session() as db:
        from config.settings import get_settings
        settings = get_settings()
        await db.execute(text(f"SET hnsw.ef_search = {settings.PGVECTOR_HNSW_EF_SEARCH}"))
        await db.execute(text("SET hnsw.iterative_scan = relaxed_order"))

        search_params: dict = {"embedding": embedding_str, "k": fetch_k}

        rows = await db.execute(
            text(f"""
                SELECT
                    dc.id,
                    dc.document_id,
                    dc.content,
                    dc.content_with_context,
                    dc.section_heading,
                    dc.page_numbers,
                    dc.department,
                    dc.document_title,
                    dc.sharepoint_url,
                    1 - (dc.embedding <=> cast(:embedding as vector)) AS score
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE d.status = 'completed'
                ORDER BY dc.embedding <=> cast(:embedding as vector)
                LIMIT :k
            """),
            search_params,
        )
        results = []
        for row in rows.fetchall():
            results.append({
                "chunk_id": str(row[0]),
                "document_id": str(row[1]),
                "content": row[2],
                "content_with_context": row[3],
                "section_heading": row[4],
                "page_numbers": row[5],
                "department": row[6],
                "document_title": row[7],
                "source": row[8],
                "score": round(float(row[9]), 4),
            })
    return results


def diversify_chunks(chunks: list[dict], top_k: int, max_per_doc: int = 3) -> list[dict]:
    """Ensure document diversity — at most max_per_doc chunks from any single document.

    Without a reranker, cosine search often returns all top-k from one document.
    This interleaves chunks from different documents so multi-entity queries
    (e.g., "ADFolks and STS") get coverage from both.
    """
    per_doc: dict[str, int] = {}
    result = []
    deferred = []

    for chunk in chunks:
        doc = chunk["document_title"] or chunk["document_id"]
        count = per_doc.get(doc, 0)
        if count < max_per_doc:
            result.append(chunk)
            per_doc[doc] = count + 1
        else:
            deferred.append(chunk)

        if len(result) >= top_k:
            break

    # Fill remaining slots from deferred if needed
    if len(result) < top_k:
        result.extend(deferred[: top_k - len(result)])

    return result


async def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """Rerank candidates with Cohere, return top_k.

    Falls back to raw vector order if Cohere is not configured.
    """
    from src.retrieval.reranker import CohereReranker
    from src.storage.vector_store import ChunkResult

    try:
        candidates = [
            ChunkResult(
                chunk_id=c["chunk_id"],
                document_id=c["document_id"],
                content=c["content"],
                content_with_context=c["content_with_context"],
                chunk_type="text",
                sequence_number=0,
                page_numbers=c["page_numbers"],
                section_heading=c["section_heading"],
                department=c["department"],
                sharepoint_url=c["source"],
                document_title=c["document_title"],
                score=c["score"],
            )
            for c in chunks
        ]

        reranker = CohereReranker()
        reranked = await reranker.rerank(query=query, candidates=candidates, top_k=top_k)

        results = []
        for r in reranked:
            c = r.chunk
            results.append({
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "content": c.content,
                "content_with_context": c.content_with_context,
                "section_heading": c.section_heading,
                "page_numbers": c.page_numbers,
                "department": c.department,
                "document_title": c.document_title,
                "source": c.sharepoint_url,
                "score": round(r.rerank_score, 4),
                "vector_score": chunks[r.original_rank]["score"],
            })
        return results

    except Exception as e:
        print(f"  [rerank] Cohere unavailable, using diversified vector order: {e}")
        return diversify_chunks(chunks, top_k)


async def get_graph_context(chunks: list[dict]) -> list[dict]:
    """Fetch related documents from Neo4j for the vector-retrieved doc set."""
    from src.knowledge_graph.builder import GraphBuilder

    doc_ids = list({c["document_id"] for c in chunks})
    if not doc_ids:
        return []

    try:
        builder = GraphBuilder()
        related = await builder.expand_from_documents(doc_ids=doc_ids, limit=5)
        return related
    except Exception as e:
        print(f"  [graph] Neo4j unavailable, skipping: {e}")
        return []


async def fetch_graph_chunks(query: str, graph_context: list[dict], already_retrieved: list[dict], top_n_per_doc: int = 3) -> list[dict]:
    """Fetch the most relevant chunks from graph-related documents.

    After Neo4j identifies related documents, this does a targeted vector search
    restricted to those document IDs — fetching top_n_per_doc chunks from EACH
    related document (not top_n total) so no single document dominates.
    """
    from sqlalchemy import text

    from src.embedding.azure_openai import AzureOpenAIEmbedder
    from src.storage.db import get_db_session

    # doc_id in Neo4j records is the PostgreSQL document UUID
    related_doc_ids = [ctx["doc_id"] for ctx in graph_context if ctx.get("doc_id")]
    already_ids = {c["document_id"] for c in already_retrieved}
    # Only fetch from docs not already in the vector results
    new_doc_ids = [d for d in related_doc_ids if d not in already_ids]
    if not new_doc_ids:
        return []

    embedder = AzureOpenAIEmbedder()
    query_embedding = await embedder.embed_query(query)
    embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

    placeholders = ", ".join(f"'{d}'" for d in new_doc_ids)

    async with get_db_session() as db:
        # Use a window function (ROW_NUMBER) to get top N per document,
        # ensuring every related document contributes its best chunks.
        graph_params: dict = {"embedding": embedding_str, "n": top_n_per_doc}

        rows = await db.execute(
            text(f"""
                SELECT
                    chunk_id, document_id, content, content_with_context,
                    section_heading, page_numbers, department, document_title,
                    sharepoint_url, score
                FROM (
                    SELECT
                        dc.id                                               AS chunk_id,
                        dc.document_id,
                        dc.content,
                        dc.content_with_context,
                        dc.section_heading,
                        dc.page_numbers,
                        dc.department,
                        dc.document_title,
                        dc.sharepoint_url,
                        1 - (dc.embedding <=> cast(:embedding as vector))  AS score,
                        ROW_NUMBER() OVER (
                            PARTITION BY dc.document_id
                            ORDER BY dc.embedding <=> cast(:embedding as vector)
                        ) AS rn
                    FROM document_chunks dc
                    JOIN documents d ON dc.document_id = d.id
                    WHERE d.status = 'completed'
                      AND dc.document_id::text IN ({placeholders})
                ) ranked
                WHERE rn <= :n
                ORDER BY score DESC
            """),
            graph_params,
        )
        results = []
        for row in rows.fetchall():
            results.append({
                "chunk_id": str(row[0]),
                "document_id": str(row[1]),
                "content": row[2],
                "content_with_context": row[3],
                "section_heading": row[4],
                "page_numbers": row[5],
                "department": row[6],
                "document_title": row[7],
                "source": row[8],
                "score": round(float(row[9]), 4),
                "via_graph": True,
            })
    return results


async def generate_answer(query: str, chunks: list[dict], graph_context: list[dict] | None = None) -> str:
    """Generate an answer from retrieved chunks using the LLM."""
    from openai import AzureOpenAI
    from config.settings import get_settings

    settings = get_settings()

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("document_title") or "Unknown"
        heading = chunk.get("section_heading") or ""
        header = f"[{i}] {title}" + (f" — {heading}" if heading else "")
        context_parts.append(f"{header}\n{chunk['content_with_context']}")

    if graph_context:
        related_parts = []
        for ctx in graph_context[:3]:
            title = ctx.get("title", "")
            summary = ctx.get("summary", "")
            rel_types = ctx.get("relationship_types") or []
            if title and summary:
                rel_label = f" ({', '.join(rel_types)})" if rel_types else ""
                related_parts.append(f"[Related{rel_label}] {title}\n{summary[:300]}")
        if related_parts:
            context_parts.append("--- Related Documents (via knowledge graph) ---\n" + "\n\n".join(related_parts))

    context = "\n\n---\n\n".join(context_parts)

    system_prompt = (
        "You are a helpful enterprise assistant. Answer the user's question based only on the "
        "provided context. Cite sources by their [number]. If the context doesn't contain enough "
        "information, say so clearly."
    )
    user_message = f"Context:\n{context}\n\nQuestion: {query}"

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
        max_completion_tokens=16384,
    )
    choice = response.choices[0]
    content = choice.message.content or ""
    if not content:
        print(f"  [DEBUG] finish_reason={choice.finish_reason}")
        print(f"  [DEBUG] refusal={getattr(choice.message, 'refusal', None)}")
        print(f"  [DEBUG] model={response.model}")
        print(f"  [DEBUG] usage={response.usage}")
    return content


async def main(query: str, top_k: int, skip_answer: bool, no_rerank: bool = False, use_hyde: bool = False, show_all: bool = False) -> None:
    print(f"\nQuery: {query}")
    print(f"{'='*60}")

    fetch_k = top_k * 4
    print(f"\nSearching top {fetch_k} candidates..." + (" (HyDE)" if use_hyde else ""))
    candidates = await search_chunks(query, fetch_k=fetch_k, use_hyde=use_hyde)

    if not candidates:
        print("\n[No results found — make sure documents have been ingested successfully]")
        return

    if show_all:
        print(f"\nAll {len(candidates)} candidate(s) (vector order):\n")
        for i, chunk in enumerate(candidates, 1):
            title = chunk.get("document_title") or "Unknown"
            heading = chunk.get("section_heading") or ""
            pages = chunk.get("page_numbers") or []
            print(f"  [{i}] vec={chunk['score']:.4f}  {title}" + (f" — {heading}" if heading else ""))
            if pages:
                print(f"       pages: {pages}")
            print(f"       {chunk['content'][:150].strip()}...")
            print()

    if no_rerank:
        chunks = candidates[:top_k]
        print(f"\nTop {len(chunks)} chunk(s) (vector order, reranking skipped):\n")
        for i, chunk in enumerate(chunks, 1):
            title = chunk.get("document_title") or "Unknown"
            heading = chunk.get("section_heading") or ""
            pages = chunk.get("page_numbers") or []
            print(f"  [{i}] vec={chunk['score']:.4f}  {title}" + (f" — {heading}" if heading else ""))
            if pages:
                print(f"       pages: {pages}")
            print(f"       {chunk['content'][:200].strip()}...")
            print()
    else:
        print(f"Reranking to top {top_k}...")
        chunks = await rerank_chunks(query, candidates, top_k=top_k)

        print(f"\nTop {len(chunks)} chunk(s) after reranking:\n")
        for i, chunk in enumerate(chunks, 1):
            title = chunk.get("document_title") or "Unknown"
            heading = chunk.get("section_heading") or ""
            pages = chunk.get("page_numbers") or []
            score = chunk["score"]
            vector_score = chunk.get("vector_score")
            score_str = f"rerank={score:.4f}" + (f"  vec={vector_score:.4f}" if vector_score is not None else f"  vec={score:.4f}")
            print(f"  [{i}] {score_str}  {title}" + (f" — {heading}" if heading else ""))
            if pages:
                print(f"       pages: {pages}")
            print(f"       {chunk['content'][:200].strip()}...")
            print()

    print("Fetching graph context...")
    graph_context = await get_graph_context(chunks)
    graph_chunks: list[dict] = []
    if graph_context:
        print(f"  Found {len(graph_context)} related document(s) via Neo4j:\n")
        for ctx in graph_context:
            rel_types = ctx.get("relationship_types") or []
            graph_score = ctx.get("max_weight") or ctx.get("graph_score") or 0
            print(f"    {ctx.get('title', '?')}  [{', '.join(rel_types)}]  score={float(graph_score):.2f}")

        graph_chunks = await fetch_graph_chunks(query, graph_context, already_retrieved=chunks, top_n_per_doc=6)
        if graph_chunks:
            print(f"\n  Fetched {len(graph_chunks)} chunk(s) from related documents (top-6 per doc):\n")
            for gc in graph_chunks:
                title = gc.get("document_title") or "Unknown"
                pages = gc.get("page_numbers") or []
                print(f"    vec={gc['score']:.4f}  {title}" + (f"  pages:{pages}" if pages else ""))
                print(f"    {gc['content'][:150].strip()}...")
        print()
    else:
        print("  No related documents found in graph.\n")

    # Merge graph chunks into context — append after vector results
    all_chunks = chunks + graph_chunks

    if not skip_answer:
        print(f"{'='*60}")
        print("Generating answer...\n")
        answer = await generate_answer(query, all_chunks, graph_context=graph_context)
        print(answer)
        print()
        print("Sources:")
        seen = set()
        for i, chunk in enumerate(all_chunks, 1):
            title = chunk.get("document_title") or "Unknown"
            if title not in seen:
                seen.add(title)
                via = "  [via graph]" if chunk.get("via_graph") else ""
                print(f"  [{i}] {title}{via}  ({chunk['source']})")


async def interactive_loop(top_k: int, skip_answer: bool, no_rerank: bool = False, use_hyde: bool = False, show_all: bool = False) -> None:
    print("\nbetter-rag local query tester")
    rerank_label = "vector order" if no_rerank else f"fetch {top_k * 4} → rerank → top-{top_k}"
    hyde_label = " + HyDE" if use_hyde else ""
    print(f"{rerank_label}{hyde_label}  answer={'off' if skip_answer else 'on'}")
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

        await main(query, top_k, skip_answer, no_rerank=no_rerank, use_hyde=use_hyde, show_all=show_all)
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test RAG queries on locally ingested documents.")
    parser.add_argument("query", nargs="?", help="Single query (omit for interactive loop)")
    parser.add_argument("--top-k", type=int, default=5, help="Number of chunks to retrieve (default: 5)")
    parser.add_argument("--no-answer", action="store_true", help="Show retrieved chunks only, skip LLM answer")
    parser.add_argument("--no-rerank", action="store_true", help="Skip Cohere reranking, use raw vector order")
    parser.add_argument("--hyde", action="store_true", help="Use HyDE: generate hypothetical answer before embedding (better for terse queries)")
    parser.add_argument("--show-all", action="store_true", help="Show all candidate chunks before reranking (debug mode)")
    args = parser.parse_args()

    if args.query:
        asyncio.run(main(args.query, args.top_k, args.no_answer, no_rerank=args.no_rerank, use_hyde=args.hyde, show_all=args.show_all))
    else:
        asyncio.run(interactive_loop(args.top_k, args.no_answer, no_rerank=args.no_rerank, use_hyde=args.hyde, show_all=args.show_all))
