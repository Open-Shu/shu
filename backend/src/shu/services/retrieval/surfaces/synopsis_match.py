"""SynopsisMatchSurface - Vector similarity search on document synopses.

Wraps VectorStore.search("synopses") to find documents whose synopsis
semantically matches the query. Useful for interpretive queries.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from ....models.document import Document
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore


class SynopsisMatchSurface(RetrievalSurface):
    """Retrieval surface for vector similarity search on document synopses.

    This surface finds documents whose high-level synopsis (generated during
    document profiling) matches the query. Particularly effective for
    interpretive or thematic queries.
    """

    name = "synopsis_match"

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
        """Search for documents with synopses similar to the query vector.

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

        results = await self._vector_store.search(
            collection="synopses",
            query_vector=query_vector,
            db=db,
            limit=limit,
            threshold=threshold,
            filters={"knowledge_base_id": str(kb_id)},
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        if not results:
            return SurfaceResult(surface_name=self.name, hits=[], execution_time_ms=elapsed_ms)

        # Load synopsis text for matched documents
        doc_ids = [r.id for r in results]
        stmt = select(Document.id, Document.synopsis).where(Document.id.in_(doc_ids))
        db_result = await db.execute(stmt)
        synopsis_map = {str(row.id): row.synopsis for row in db_result.fetchall()}

        # For synopses collection, the id is already the document_id
        hits = [
            SurfaceHit(
                id=UUID(r.id),
                id_type="document",
                score=r.score,
                metadata={"synopsis": synopsis_map.get(r.id) or ""},
            )
            for r in results
        ]

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
