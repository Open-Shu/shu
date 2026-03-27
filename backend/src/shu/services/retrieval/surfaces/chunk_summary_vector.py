"""ChunkSummaryVectorSurface - Vector similarity on LLM-generated chunk summaries.

Wraps VectorStore.search("chunk_summaries") to find chunks whose profiled
summary embedding is semantically similar to the query. Summaries are
query-encoded (short, ~1 sentence) so this surface excels at matching
casual/conversational queries without the noise of raw chunk content.

See SHU-632.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore


class ChunkSummaryVectorSurface(RetrievalSurface):
    """Retrieval surface for vector similarity on chunk summary embeddings."""

    name = "chunk_summary"

    def __init__(self, vector_store: VectorStore) -> None:
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
        """Search for chunks whose summary embedding matches the query vector."""
        start = time.perf_counter()

        results = await self._vector_store.search(
            collection="chunk_summaries",
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
