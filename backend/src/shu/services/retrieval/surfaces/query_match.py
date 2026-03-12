"""QueryMatchSurface - Vector similarity search on synthesized queries.

Wraps VectorStore.search("queries") to find documents whose synthesized
queries match the user's query. This is the novel contribution of
multi-surface search — matching user intent against pre-computed
hypothetical queries rather than raw content.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from ....models.document import DocumentQuery
from ..protocol import SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore


class QueryMatchSurface:
    """Retrieval surface for vector similarity search on synthesized queries.

    This surface finds documents whose synthesized queries (generated during
    document profiling) match the user's query. Particularly effective for
    interpretive queries like "Why did we choose X?" or structural queries
    like "What topics does this cover?".

    Unlike synopsis_match which returns document_ids directly, this surface:
    1. Searches the queries collection (returns query_ids)
    2. Looks up DocumentQuery records to get document_id and query_text
    3. Aggregates by document_id using max score
    4. Includes the matched query text in metadata for provenance
    """

    name = "query_match"

    def __init__(self, vector_store: VectorStore) -> None:
        """Initialize with a VectorStore instance.

        Args:
            vector_store: The vector store to search against.

        """
        self._vector_store = vector_store

    async def search(
        self,
        query_text: str,
        query_vector: list[float],
        keyword_terms: list[str],
        *,
        kb_id: UUID,
        limit: int = 50,
        threshold: float = 0.0,
        db: AsyncSession,
    ) -> SurfaceResult:
        """Search for documents with synthesized queries similar to the user query.

        Args:
            query_text: Original query text (unused by this surface).
            query_vector: Pre-computed embedding vector.
            keyword_terms: Keyword terms (unused by this surface).
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of documents to return.
            threshold: Minimum similarity score (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with document hits and execution time.

        """
        start = time.perf_counter()

        # Step 1: Search queries collection for matching query embeddings
        results = await self._vector_store.search(
            collection="queries",
            query_vector=query_vector,
            db=db,
            limit=limit * 3,  # Fetch more since we aggregate by document
            threshold=threshold,
            filters={"knowledge_base_id": str(kb_id)},
        )

        if not results:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Step 2: Fetch DocumentQuery records to get document_id and query_text
        query_ids = [r.id for r in results]
        score_by_query_id = {r.id: r.score for r in results}

        stmt = select(DocumentQuery.id, DocumentQuery.document_id, DocumentQuery.query_text).where(
            DocumentQuery.id.in_(query_ids)
        )
        db_result = await db.execute(stmt)
        query_records = db_result.fetchall()

        # Step 3: Aggregate by document_id using max score, keeping best query_text
        doc_best: dict[str, tuple[float, str]] = {}  # document_id -> (best_score, query_text)
        for query_id, document_id, query_text_val in query_records:
            score = score_by_query_id.get(str(query_id), 0.0)
            if document_id not in doc_best or score > doc_best[document_id][0]:
                doc_best[document_id] = (score, query_text_val)

        # Step 4: Build hits sorted by score, limited to requested count
        sorted_docs = sorted(doc_best.items(), key=lambda x: x[1][0], reverse=True)[:limit]

        hits = [
            SurfaceHit(
                id=UUID(doc_id),
                id_type="document",
                score=score,
                metadata={"matched_query": matched_query},
            )
            for doc_id, (score, matched_query) in sorted_docs
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
