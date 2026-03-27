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
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore


class QueryMatchSurface(RetrievalSurface):
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
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of documents to return.
            threshold: Minimum similarity score (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with document hits and execution time.

        """
        start = time.perf_counter()

        # Steps 1-3: Page through vector results until we have `limit` distinct documents.
        # A fixed multiplier can underfill when one document dominates the top-k slots.
        page_size = limit * 3
        max_pages = 5  # guard against looping on very small corpora
        doc_best: dict[str, tuple[float, str, str | None]] = {}  # doc_id -> (score, query_text, source_chunk_id)

        for page in range(max_pages):
            offset = page * page_size
            page_results = await self._vector_store.search(
                collection="queries",
                query_vector=query_vector,
                db=db,
                limit=page_size,
                threshold=threshold,
                filters={"knowledge_base_id": str(kb_id)},
                offset=offset,
            )

            if not page_results:
                break

            score_by_query_id = {r.id: r.score for r in page_results}
            query_ids = [r.id for r in page_results]

            stmt = select(
                DocumentQuery.id,
                DocumentQuery.document_id,
                DocumentQuery.query_text,
                DocumentQuery.source_chunk_id,
            ).where(DocumentQuery.id.in_(query_ids))
            db_result = await db.execute(stmt)
            query_records = db_result.fetchall()

            for query_id, document_id, query_text_val, source_chunk_id in query_records:
                score = score_by_query_id.get(str(query_id), 0.0)
                if document_id not in doc_best or score > doc_best[document_id][0]:
                    doc_best[document_id] = (score, query_text_val, source_chunk_id)

            if len(doc_best) >= limit or len(page_results) < page_size:
                break

        if not doc_best:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Step 4: Build hits sorted by score, limited to requested count
        sorted_docs = sorted(doc_best.items(), key=lambda x: x[1][0], reverse=True)[:limit]

        hits = []
        for doc_id, (score, matched_query, source_chunk_id) in sorted_docs:
            meta: dict[str, str] = {"matched_query": matched_query}
            if source_chunk_id:
                meta["source_chunk_id"] = str(source_chunk_id)
            hits.append(
                SurfaceHit(
                    id=UUID(doc_id),
                    id_type="document",
                    score=score,
                    metadata=meta,
                )
            )

        elapsed_ms = (time.perf_counter() - start) * 1000

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
