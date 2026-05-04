"""Customer Care vector store — HNSW search against cc_document_chunks with RBAC."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
import structlog
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.customer_care.models import CCDocumentChunk
from src.storage.vector_store import ChunkResult

logger = structlog.get_logger()


@dataclass
class CCChunkResult(ChunkResult):
    """A CC search result — extends ChunkResult with CC-specific fields."""

    category: str | None = None
    policy_url: str | None = None


class CCVectorStore:
    """
    Vector store for the Customer Care knowledge base.

    Drop-in compatible interface with PgVectorStore but queries
    cc_document_chunks / cc_document_user_access tables.
    """

    def __init__(self, session: AsyncSession | None = None):
        self._session = session
        self.settings = get_settings()
        self._hnsw_configured = False

    async def _get_session(self) -> AsyncSession:
        if self._session is not None:
            return self._session
        raise RuntimeError(
            "CCVectorStore requires an explicit session. "
            "Use `async with get_db_session() as db: store = CCVectorStore(db)`"
        )

    async def configure_search(self, session: AsyncSession) -> None:
        """Set per-session HNSW ef_search for high recall (once per instance)."""
        if self._hnsw_configured:
            return
        await session.execute(
            text(f"SET hnsw.ef_search = {self.settings.PGVECTOR_HNSW_EF_SEARCH}")
        )
        await session.execute(text("SET hnsw.iterative_scan = relaxed_order"))
        self._hnsw_configured = True

    # ── Write Operations ──

    async def upsert_chunks(
        self,
        document,  # CCDocument instance
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> list[uuid.UUID]:
        """
        Insert chunks with embeddings for a CC document.

        Inherits category and policy_url from the parent CCDocument.
        Returns list of generated chunk UUIDs (same order as input).
        """
        session = await self._get_session()

        chunk_models = []
        for i, (chunk_data, embedding) in enumerate(zip(chunks, embeddings)):
            chunk = CCDocumentChunk(
                id=uuid.uuid4(),
                document_id=document.id,
                content=chunk_data["content"],
                content_with_context=chunk_data["content_with_context"],
                chunk_type=chunk_data.get("chunk_type", "text"),
                sequence_number=chunk_data.get("sequence_number", i),
                page_numbers=chunk_data.get("page_numbers"),
                section_heading=chunk_data.get("section_heading"),
                token_count=chunk_data.get("token_count"),
                embedding=embedding,
                access_level=chunk_data.get("access_level"),
                sharepoint_url=document.sharepoint_url,
                document_title=document.name,
                # CC-specific — inherited from parent document
                category=document.category,
                policy_url=document.policy_url or document.sharepoint_url,
            )
            chunk_models.append(chunk)

        session.add_all(chunk_models)
        await session.flush()

        logger.info(
            "cc_vector_store.upsert_chunks",
            document_id=str(document.id),
            chunk_count=len(chunk_models),
        )
        return [cm.id for cm in chunk_models]

    async def delete_by_document(self, document_id: str) -> int:
        """Delete all CC chunks for a document."""
        session = await self._get_session()
        result = await session.execute(
            delete(CCDocumentChunk).where(
                CCDocumentChunk.document_id == uuid.UUID(document_id)
            )
        )
        count = result.rowcount
        logger.info(
            "cc_vector_store.delete_chunks",
            document_id=document_id,
            deleted=count,
        )
        return count

    # ── Search Operations ──

    async def cosine_search(
        self,
        query_embedding: list[float],
        user_id: str,
        k: int = 20,
        department: str | None = None,    # alias for category — used by RetrievalPipeline
        content_type: str | None = None,   # unused in CC, kept for interface compat
        category: str | None = None,       # CC-specific category filter
    ) -> list[CCChunkResult]:
        """
        RBAC-filtered cosine similarity search against cc_document_chunks.

        Every search is filtered by the agent's document access
        (via JOIN on cc_document_user_access).
        The `department` parameter is treated as `category` for pipeline compatibility.
        """
        session = await self._get_session()
        await self.configure_search(session)

        effective_category = category or department

        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        filters = ["cua.user_id = :user_id"]
        params: dict = {"user_id": user_id, "k": k, "embedding": embedding_str}

        if effective_category:
            filters.append("cc.category = :category")
            params["category"] = effective_category

        where_clause = " AND ".join(filters)

        query = text(f"""
            SELECT
                cc.id,
                cc.document_id,
                cc.content,
                cc.content_with_context,
                cc.chunk_type,
                cc.sequence_number,
                cc.page_numbers,
                cc.section_heading,
                cc.category,
                cc.sharepoint_url,
                cc.document_title,
                cc.policy_url,
                1 - (cc.embedding <=> :embedding::vector) AS score
            FROM cc_document_chunks cc
            JOIN cc_document_user_access cua ON cc.document_id = cua.document_id
            WHERE {where_clause}
            ORDER BY cc.embedding <=> :embedding::vector
            LIMIT :k
        """)

        result = await session.execute(query, params)
        rows = result.fetchall()

        return [
            CCChunkResult(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                content=row[2],
                content_with_context=row[3],
                chunk_type=row[4],
                sequence_number=row[5],
                page_numbers=row[6],
                section_heading=row[7],
                department=row[8],   # category stored in department slot for compat
                sharepoint_url=row[9],
                document_title=row[10],
                score=float(row[12]),
                # CC-specific
                category=row[8],
                policy_url=row[11],
            )
            for row in rows
        ]

    async def mmr_search(
        self,
        query_embedding: list[float],
        user_id: str,
        k: int = 10,
        fetch_k: int = 60,
        lambda_mult: float = 0.7,
        department: str | None = None,
        category: str | None = None,
    ) -> list[CCChunkResult]:
        """
        Maximal Marginal Relevance search against cc_document_chunks.

        Fetches a large candidate set via cosine search, then applies
        MMR selection in Python to balance relevance vs diversity.
        Embedding lookup queries cc_document_chunks (not document_chunks).
        """
        candidates = await self.cosine_search(
            query_embedding=query_embedding,
            user_id=user_id,
            k=fetch_k,
            department=department,
            category=category,
        )

        if not candidates:
            return []

        session = await self._get_session()
        chunk_ids = [c.chunk_id for c in candidates]
        id_placeholders = ", ".join(f"'{cid}'" for cid in chunk_ids)

        emb_result = await session.execute(
            text(f"""
                SELECT id, embedding
                FROM cc_document_chunks
                WHERE id::text IN ({id_placeholders})
            """)
        )
        emb_rows = emb_result.fetchall()
        emb_map = {str(row[0]): np.array(row[1]) for row in emb_rows}

        query_vec = np.array(query_embedding)

        selected: list[CCChunkResult] = []
        candidate_pool = list(candidates)

        while len(selected) < k and candidate_pool:
            best_idx = -1
            best_score = -float("inf")

            for i, cand in enumerate(candidate_pool):
                cand_emb = emb_map.get(cand.chunk_id)
                if cand_emb is None:
                    continue

                relevance = float(np.dot(query_vec, cand_emb) / (
                    np.linalg.norm(query_vec) * np.linalg.norm(cand_emb) + 1e-10
                ))

                if selected:
                    selected_embs = [
                        emb_map[s.chunk_id]
                        for s in selected
                        if s.chunk_id in emb_map
                    ]
                    if selected_embs:
                        sims = [
                            float(np.dot(cand_emb, se) / (
                                np.linalg.norm(cand_emb) * np.linalg.norm(se) + 1e-10
                            ))
                            for se in selected_embs
                        ]
                        max_sim = max(sims)
                    else:
                        max_sim = 0.0
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

        return selected
