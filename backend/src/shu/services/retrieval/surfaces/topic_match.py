"""TopicMatchSurface - stub, not implemented.

Topics are stored as multi-word phrases which are incompatible with JSONB
exact-match. The chunk summary embedding surface (SHU-632) covers the same
semantic space, making a dedicated topic surface redundant. This stub
preserves the protocol contract for potential future use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ..protocol import RetrievalSurface, SurfaceResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TopicMatchSurface(RetrievalSurface):
    """Stub — not implemented. ChunkSummaryVectorSurface covers this use case."""

    name = "topic_match"

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
        """Return empty results immediately."""
        return SurfaceResult(surface_name=self.name, hits=[], execution_time_ms=0.0)
