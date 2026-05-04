"""PgVector store — HNSW search with RBAC-filtered retrieval."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
import structlog
from pgvector.sqlalchemy import Vector
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from src.models.document import Document, DocumentChunk, DocumentUserAccess
from src.models.enums import ChunkType
from src.storage.db import get_db_session

logger = structlog.get_logger()


@dataclass
class ChunkResult:
    """A single search result with metadata."""

    chunk_id: str
    document_id: str
    content: str
    content_with_context: str
    chunk_type: str
    sequence_number: int
    page_numbers: list[int] | None
    section_heading: str | None
    department: str | None
    sharepoint_url: str | None
    document_title: str | None
    score: float
    extraction_method: str | None = None


class PgVectorStore:
    """Vector store backed by PostgreSQL + pgvector with HNSW index."""

    def __init__(self, session: AsyncSession | None = None):
        self._session = session
        self.settings = get_settings()
        self._hnsw_configured = False

    async def _get_session(self) -> AsyncSession:
        if self._session is not None:
            return self._session
        raise RuntimeError(
            "PgVectorStore requires an explicit session. "
            "Use `async with get_db_session() as db: store = PgVectorStore(db)`"
        )

    async def configure_search(self, session: AsyncSession) -> None:
        """Set per-session HNSW search parameters for high recall (once per instance)."""
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
        document: Document,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> list[uuid.UUID]:
        """
        Insert chunks with embeddings for a document.

        Args:
            document: The parent Document model instance.
            chunks: List of chunk dicts with keys: content, content_with_context,
                    chunk_type, sequence_number, page_numbers, section_heading, token_count.
            embeddings: Parallel list of embedding vectors.

        Returns:
            List of generated chunk UUIDs (same order as input).
        """
        session = await self._get_session()

        chunk_models = []
        for i, (chunk_data, embedding) in enumerate(zip(chunks, embeddings)):
            chunk = DocumentChunk(
                id=uuid.uuid4(),
                document_id=document.id,
                content=chunk_data["content"],
                content_with_context=chunk_data["content_with_context"],
                chunk_type=chunk_data.get("chunk_type", ChunkType.TEXT),
                sequence_number=chunk_data.get("sequence_number", i),
                page_numbers=chunk_data.get("page_numbers"),
                section_heading=chunk_data.get("section_heading"),
                token_count=chunk_data.get("token_count"),
                embedding=embedding,
                # Denormalized fields
                extraction_method=chunk_data.get("extraction_method", document.extraction_method),
                department=document.department,
                access_level=chunk_data.get("access_level"),
                content_type=document.content_type_tag,
                sharepoint_url=document.sharepoint_url,
                document_title=document.name,
            )
            chunk_models.append(chunk)

        session.add_all(chunk_models)
        await session.flush()

        logger.info(
            "vector_store.upsert_chunks",
            document_id=str(document.id),
            chunk_count=len(chunk_models),
        )
        return [cm.id for cm in chunk_models]

    async def delete_by_document(self, document_id: str) -> int:
        """Delete all chunks for a document."""
        session = await self._get_session()
        result = await session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id == uuid.UUID(document_id)
            )
        )
        count = result.rowcount
        logger.info(
            "vector_store.delete_chunks",
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
        department: str | None = None,
        content_type: str | None = None,
        extraction_method: str | None = None,
    ) -> list[ChunkResult]:
        """
        RBAC-filtered cosine similarity search via pgvector HNSW.

        Every search is automatically filtered by the user's document access
        (via JOIN on document_user_access).
        """
        session = await self._get_session()
        await self.configure_search(session)

        # Build the query — skip RBAC JOIN when user_id is "anonymous" (testing)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        skip_rbac = user_id == "anonymous"
        filters: list[str] = []
        params: dict = {"k": k, "embedding": embedding_str}

        if not skip_rbac:
            filters.append("dua.user_id = :user_id")
            params["user_id"] = user_id

        if department:
            filters.append("dc.department = :department")
            params["department"] = department

        if content_type:
            filters.append("dc.content_type = :content_type")
            params["content_type"] = content_type

        if extraction_method:
            filters.append("dc.extraction_method = :extraction_method")
            params["extraction_method"] = extraction_method

        where_clause = " AND ".join(filters) if filters else "TRUE"
        rbac_join = "" if skip_rbac else "JOIN document_user_access dua ON dc.document_id = dua.document_id"

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
                1 - (dc.embedding <=> cast(:embedding as vector)) AS score
            FROM document_chunks dc
            {rbac_join}
            WHERE {where_clause}
            ORDER BY dc.embedding <=> cast(:embedding as vector)
            LIMIT :k
        """)

        result = await session.execute(query, params)
        rows = result.fetchall()

        return [
            ChunkResult(
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
            for row in rows
        ]

    async def search_by_doc_ids(
        self,
        query_embedding: list[float],
        doc_ids: list[str],
        top_n_per_doc: int = 3,
    ) -> list[ChunkResult]:
        """
        Targeted vector search restricted to specific document IDs.

        Uses ROW_NUMBER per document so each related document contributes
        its best chunks (not dominated by one document).
        """
        if not doc_ids:
            return []

        session = await self._get_session()
        await self.configure_search(session)

        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
        placeholders = ", ".join(f"'{d}'" for d in doc_ids)

        result = await session.execute(
            text(f"""
                SELECT
                    chunk_id, document_id, content, content_with_context,
                    chunk_type, sequence_number, page_numbers, section_heading,
                    department, sharepoint_url, document_title, score
                FROM (
                    SELECT
                        dc.id                                              AS chunk_id,
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
            {"embedding": embedding_str, "n": top_n_per_doc},
        )
        rows = result.fetchall()

        return [
            ChunkResult(
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
            for row in rows
        ]

    @staticmethod
    def diversify(
        chunks: list[ChunkResult], top_k: int, max_per_doc: int = 3,
    ) -> list[ChunkResult]:
        """Ensure document diversity — at most max_per_doc chunks from any single document."""
        per_doc: dict[str, int] = {}
        result: list[ChunkResult] = []
        deferred: list[ChunkResult] = []

        for chunk in chunks:
            doc = chunk.document_title or chunk.document_id
            count = per_doc.get(doc, 0)
            if count < max_per_doc:
                result.append(chunk)
                per_doc[doc] = count + 1
            else:
                deferred.append(chunk)
            if len(result) >= top_k:
                break

        if len(result) < top_k:
            result.extend(deferred[: top_k - len(result)])

        return result

    async def mmr_search(
        self,
        query_embedding: list[float],
        user_id: str,
        k: int = 10,
        fetch_k: int = 60,
        lambda_mult: float = 0.7,
        department: str | None = None,
        extraction_method: str | None = None,
    ) -> list[ChunkResult]:
        """
        Maximal Marginal Relevance search (application-level).

        1. Fetch a large candidate set via cosine search (fetch_k).
        2. Apply MMR selection in Python to balance relevance vs diversity.
        """
        candidates = await self.cosine_search(
            query_embedding=query_embedding,
            user_id=user_id,
            k=fetch_k,
            department=department,
            extraction_method=extraction_method,
        )

        if not candidates:
            return []

        # Extract embeddings for MMR computation
        session = await self._get_session()
        chunk_ids = [c.chunk_id for c in candidates]
        id_placeholders = ", ".join(f"'{cid}'" for cid in chunk_ids)

        emb_result = await session.execute(
            text(f"""
                SELECT id, embedding
                FROM document_chunks
                WHERE id::text IN ({id_placeholders})
            """)
        )
        emb_rows = emb_result.fetchall()
        emb_map = {str(row[0]): np.array(row[1]) for row in emb_rows}

        query_vec = np.array(query_embedding)

        # MMR selection
        selected: list[ChunkResult] = []
        candidate_pool = list(candidates)

        while len(selected) < k and candidate_pool:
            best_idx = -1
            best_score = -float("inf")

            for i, cand in enumerate(candidate_pool):
                cand_emb = emb_map.get(cand.chunk_id)
                if cand_emb is None:
                    continue

                # Relevance to query
                relevance = float(np.dot(query_vec, cand_emb) / (
                    np.linalg.norm(query_vec) * np.linalg.norm(cand_emb) + 1e-10
                ))

                # Max similarity to already selected
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
