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

    When source_chunk_id is available (KBs profiled with SHU-645+), this
    surface emits chunk-level hits so the matched chunk appears as a
    contributing chunk in fused results. Falls back to document-level hits
    for older KBs without chunk provenance.

    Steps:
    1. Searches the queries collection (returns query_ids)
    2. Looks up DocumentQuery records to get source_chunk_id and query_text
    3. Aggregates by source_chunk_id (best score per chunk), falls back to document_id
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

        # Steps 1-3: Page through vector results until we have `limit` distinct
        # entities (chunks when source_chunk_id is available, documents otherwise).
        page_size = limit * 3
        max_pages = 5  # guard against looping on very small corpora

        # Aggregate by source_chunk_id when available, document_id otherwise.
        # chunk_best: source_chunk_id -> (score, query_text, document_id)
        chunk_best: dict[str, tuple[float, str, str]] = {}
        # doc_best: document_id -> (score, query_text) — fallback for null source_chunk_id
        doc_best: dict[str, tuple[float, str]] = {}

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
                if source_chunk_id:
                    if source_chunk_id not in chunk_best or score > chunk_best[source_chunk_id][0]:
                        chunk_best[source_chunk_id] = (score, query_text_val, document_id)
                elif document_id not in doc_best or score > doc_best[document_id][0]:
                    doc_best[document_id] = (score, query_text_val)

            total_entities = len(chunk_best) + len(doc_best)
            if total_entities >= limit or len(page_results) < page_size:
                break

        if not chunk_best and not doc_best:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Step 4: Build hits — chunk-level when provenance exists, document-level otherwise
        hits: list[SurfaceHit] = []

        for chunk_id, (score, matched_query, _document_id) in chunk_best.items():
            hits.append(
                SurfaceHit(
                    id=UUID(chunk_id),
                    id_type="chunk",
                    score=score,
                    metadata={"matched_query": matched_query},
                )
            )

        for doc_id, (score, matched_query) in doc_best.items():
            hits.append(
                SurfaceHit(
                    id=UUID(doc_id),
                    id_type="document",
                    score=score,
                    metadata={"matched_query": matched_query},
                )
            )

        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[:limit]

        elapsed_ms = (time.perf_counter() - start) * 1000

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
