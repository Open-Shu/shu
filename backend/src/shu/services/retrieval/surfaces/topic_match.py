"""TopicMatchSurface - stub for future topic-based retrieval.

Topics are stored as multi-word phrases (e.g., "Intranasal dosing protocol")
which are incompatible with the JSONB ?| exact-match approach used by
KeywordMatchSurface. A future implementation could use embedding-based
semantic similarity on the topics column, but the planned chunk summary
embedding surface (SHU-632) likely covers the same semantic space, making
a dedicated topic surface redundant. Keeping the stub so the surface
protocol contract is preserved and can be revisited if needed.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from ....core.logging import get_logger
from ..protocol import RetrievalSurface, SurfaceResult

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TopicMatchSurface(RetrievalSurface):
    """Stub — not yet implemented. See module docstring."""

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
        """Return empty results. Topic surface is not yet implemented."""
        elapsed_ms = (time.perf_counter() - time.perf_counter()) * 1000
        return SurfaceResult(
            surface_name=self.name,
            hits=[],
            execution_time_ms=elapsed_ms,
        )
