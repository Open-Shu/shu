"""MultiSurfaceSearchService - Orchestrator for multi-surface retrieval.

Coordinates parallel execution of multiple retrieval surfaces and
delegates score fusion to ScoreFusionService.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import UUID

from ...core.logging import get_logger
from .protocol import FusedResult, RetrievalSurface, SurfaceResult
from .score_fusion import ScoreFusionService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.embedding_service import EmbeddingService

logger = get_logger(__name__)

# Default configuration
DEFAULT_SURFACE_LIMIT = 50
DEFAULT_TIMEOUT_MS = 2000


class MultiSurfaceSearchService:
    """Orchestrator for multi-surface retrieval.

    Executes multiple retrieval surfaces in parallel and fuses their
    results into a ranked list of documents.
    """

    def __init__(
        self,
        surfaces: list[RetrievalSurface],
        embedding_service: EmbeddingService,
        fusion_service: ScoreFusionService | None = None,
        *,
        surface_limit: int = DEFAULT_SURFACE_LIMIT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        """Initialize the multi-surface search service.

        Args:
            surfaces: List of retrieval surfaces to execute.
            embedding_service: Service for generating query embeddings.
            fusion_service: Service for fusing results. If None, creates default.
            surface_limit: Max results per surface.
            timeout_ms: Timeout for surface execution in milliseconds.

        """
        self._surfaces = surfaces
        self._embedding_service = embedding_service
        self._fusion_service = fusion_service or ScoreFusionService()
        self._surface_limit = surface_limit
        self._timeout_ms = timeout_ms

    async def search(
        self,
        query: str,
        kb_id: UUID,
        *,
        keyword_terms: list[str],
        limit: int = 10,
        threshold: float = 0.0,
        db: AsyncSession,
    ) -> list[FusedResult]:
        """Execute multi-surface search and return fused results.

        Args:
            query: The search query text.
            kb_id: Knowledge base ID to scope the search.
            keyword_terms: Pre-extracted keyword terms from query preprocessing.
            limit: Maximum number of documents to return.
            threshold: Minimum final score threshold.
            db: Async database session.

        Returns:
            List of FusedResult sorted by final_score descending.

        """
        start_time = time.perf_counter()

        # Step 1: Generate query embedding
        query_vector = await self._embedding_service.embed_query(query)

        # Step 2: Execute all surfaces in parallel
        tasks = [
            self._execute_surface(
                surface,
                query_text=query,
                query_vector=query_vector,
                keyword_terms=keyword_terms,
                kb_id=kb_id,
                db=db,
            )
            for surface in self._surfaces
        ]

        surface_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Step 3: Filter out exceptions and collect valid results
        valid_results: list[SurfaceResult] = []
        for i, result in enumerate(surface_results):
            if isinstance(result, Exception):
                surface_name = self._surfaces[i].name
                logger.warning(
                    "Surface execution failed",
                    extra={
                        "surface": surface_name,
                        "error": str(result),
                        "error_type": type(result).__name__,
                    },
                )
            elif isinstance(result, SurfaceResult):
                valid_results.append(result)

        if not valid_results:
            logger.warning(
                "All surfaces failed or returned no results",
                extra={"query": query[:100], "kb_id": str(kb_id)},
            )
            return []

        # Step 5: Fuse results
        fused_results = await self._fusion_service.fuse(
            valid_results,
            limit=limit,
            threshold=threshold,
            db=db,
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Multi-surface search completed",
            extra={
                "query": query[:100],
                "kb_id": str(kb_id),
                "surfaces_executed": len(valid_results),
                "results_returned": len(fused_results),
                "execution_time_ms": round(elapsed_ms, 2),
            },
        )

        return fused_results

    async def _execute_surface(
        self,
        surface: RetrievalSurface,
        *,
        query_text: str,
        query_vector: list[float],
        keyword_terms: list[str],
        kb_id: UUID,
        db: AsyncSession,
    ) -> SurfaceResult:
        """Execute a single surface with timeout.

        Args:
            surface: The surface to execute.
            query_text: Original query text.
            query_vector: Pre-computed query embedding.
            keyword_terms: Extracted keyword terms.
            kb_id: Knowledge base ID.
            db: Database session.

        Returns:
            SurfaceResult from the surface.

        Raises:
            asyncio.TimeoutError: If surface execution exceeds timeout.

        """
        timeout_seconds = self._timeout_ms / 1000

        return await asyncio.wait_for(
            surface.search(
                query_text,
                query_vector,
                keyword_terms,
                kb_id=kb_id,
                limit=self._surface_limit,
                threshold=0.0,  # Let fusion handle threshold
                db=db,
            ),
            timeout=timeout_seconds,
        )
