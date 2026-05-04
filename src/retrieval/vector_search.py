"""Vector search strategies — cosine similarity and MMR."""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.retrieval.metadata_filter import MetadataFilter
from src.storage.vector_store import ChunkResult, PgVectorStore

logger = structlog.get_logger()


class VectorSearchEngine:
    """
    Executes vector similarity searches against pgvector with RBAC and metadata filtering.

    Wraps PgVectorStore with additional:
    - Full MetadataFilter application (date range, content_type, access_level)
    - MMR diversity selection
    - Configurable ef_search for recall tuning
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = PgVectorStore(session)
        self.settings = get_settings()
        self._hnsw_configured = False

    async def cosine_search(
        self,
        query_embedding: list[float],
        user_id: str,
        filters: MetadataFilter,
        k: int = 20,
    ) -> list[ChunkResult]:
        """
        RBAC-filtered cosine similarity search with full metadata filtering.

        Args:
            query_embedding: The query embedding vector.
            user_id: Entra ID user ID for RBAC filtering.
            filters: Metadata pre-filters to apply.
            k: Number of results to return.

        Returns:
            List of ChunkResult ordered by cosine similarity (descending).
        """
        await self._configure_hnsw()

        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        where_clauses = ["dua.user_id = :user_id"]
        params: dict[str, Any] = {"user_id": user_id, "k": k, "embedding": embedding_str}

        for clause in filters.to_sql_clauses():
            where_clauses.append(clause)

        filter_params = filters.to_sql_params()
        params.update(filter_params)

        where_sql = " AND ".join(where_clauses)

        query = text(f"""
            SELECT
                dc.id,
                dc.document_id,
                dc.content,
                dc.content_with_context,
                dc.chunk_type,
                dc.sequence_number,
                dc.page_numbers,
                dc.section_heading,
                dc.department,
                dc.sharepoint_url,
                dc.document_title,
                1 - (dc.embedding <=> :embedding::vector) AS score
            FROM document_chunks dc
            JOIN document_user_access dua ON dc.document_id = dua.document_id
            WHERE {where_sql}
            ORDER BY dc.embedding <=> :embedding::vector
            LIMIT :k
        """)

        result = await self._session.execute(query, params)
        rows = result.fetchall()

        results = [_row_to_chunk_result(row) for row in rows]

        logger.info(
            "vector_search.cosine_complete",
            user_id=user_id,
            k=k,
            returned=len(results),
            filters_active=not filters.is_empty,
        )
        return results

    async def mmr_search(
        self,
        query_embedding: list[float],
        user_id: str,
        filters: MetadataFilter,
        k: int = 10,
        fetch_k: int = 60,
        lambda_mult: float = 0.7,
    ) -> list[ChunkResult]:
        """
        Maximal Marginal Relevance search — balances relevance with diversity.

        1. Fetch fetch_k candidates via cosine search.
        2. Apply MMR selection in Python to maximize relevance while minimizing redundancy.

        Args:
            lambda_mult: 1.0 = pure relevance, 0.0 = pure diversity.
        """
        candidates = await self.cosine_search(
            query_embedding=query_embedding,
            user_id=user_id,
            filters=filters,
            k=fetch_k,
        )

        if not candidates:
            return []

        # Fetch embeddings for candidates from DB
        chunk_ids = [c.chunk_id for c in candidates]
        id_placeholders = ", ".join(f"'{cid}'" for cid in chunk_ids)

        emb_result = await self._session.execute(
            text(f"""
                SELECT id, embedding
                FROM document_chunks
                WHERE id::text IN ({id_placeholders})
            """)
        )
        emb_rows = emb_result.fetchall()
        emb_map = {str(row[0]): np.array(row[1]) for row in emb_rows}

        query_vec = np.array(query_embedding)
        selected: list[ChunkResult] = []
        candidate_pool = list(candidates)

        while len(selected) < k and candidate_pool:
            best_idx = -1
            best_score = -float("inf")

            for i, cand in enumerate(candidate_pool):
                cand_emb = emb_map.get(cand.chunk_id)
                if cand_emb is None:
                    continue

                relevance = _cosine_sim(query_vec, cand_emb)

                if selected:
                    max_sim = max(
                        _cosine_sim(cand_emb, emb_map[s.chunk_id])
                        for s in selected
                        if s.chunk_id in emb_map
                    ) if any(s.chunk_id in emb_map for s in selected) else 0.0
                else:
                    max_sim = 0.0

                mmr_score = lambda_mult * relevance - (1 - lambda_mult) * max_sim
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i

            if best_idx >= 0:
                selected.append(candidate_pool.pop(best_idx))
            else:
                break

        logger.info(
            "vector_search.mmr_complete",
            user_id=user_id,
            candidates=len(candidates),
            selected=len(selected),
        )
        return selected

    async def _configure_hnsw(self) -> None:
        """Set per-session HNSW parameters for high recall (once per instance)."""
        if self._hnsw_configured:
            return
        ef = self.settings.PGVECTOR_HNSW_EF_SEARCH
        await self._session.execute(text(f"SET hnsw.ef_search = {ef}"))
        await self._session.execute(text("SET hnsw.iterative_scan = relaxed_order"))
        self._hnsw_configured = True


def _row_to_chunk_result(row) -> ChunkResult:
    return ChunkResult(
        chunk_id=str(row[0]),
        document_id=str(row[1]),
        content=row[2],
        content_with_context=row[3],
        chunk_type=row[4],
        sequence_number=row[5],
        page_numbers=row[6],
        section_heading=row[7],
        department=row[8],
        sharepoint_url=row[9],
        document_title=row[10],
        score=float(row[11]),
    )


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))
