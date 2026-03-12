"""ChunkVectorSurface - Vector similarity search on document chunks.

Wraps VectorStore.search("chunks") to find chunks with content that
semantically matches the query.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore


class ChunkVectorSurface(RetrievalSurface):
    """Retrieval surface for vector similarity search on chunks.

    This is the primary retrieval surface, finding chunks whose embeddings
    are similar to the query embedding.
    """

    name = "chunk_vector"

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
        """Search for chunks similar to the query vector.

        Args:
            query_text: Original query text (unused by this surface).
            query_vector: Pre-computed embedding vector.
            keyword_terms: Keyword terms (unused by this surface).
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of chunks to return.
            threshold: Minimum similarity score (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with chunk hits and execution time.

        """
        start = time.perf_counter()

        results = await self._vector_store.search(
            collection="chunks",
            query_vector=query_vector,
            db=db,
            limit=limit,
            threshold=threshold,
            filters={"knowledge_base_id": str(kb_id)},
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        hits = [
            SurfaceHit(
                id=UUID(r.id),
                id_type="chunk",
                score=r.score,
                metadata={},
            )
            for r in results
        ]

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
