"""TopicMatchSurface - JSONB matching on profiled chunk topics.

Queries the topics JSONB field on document_chunks to find chunks
about conceptual categories matching the user's query. Uses match
ratio scoring: score = len(matched_terms) / len(query_terms).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Text, cast, select
from sqlalchemy.dialects.postgresql import ARRAY

from ....core.logging import get_logger
from ....models.document import DocumentChunk
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TopicMatchSurface(RetrievalSurface):
    """Retrieval surface for JSONB topic matching on chunks.

    This surface finds chunks whose profiled topics overlap with terms
    extracted from the user query. Topics are broader conceptual labels
    (e.g., "authentication", "database design") assigned during profiling.

    For MVP, reuses the same keyword extraction as KeywordMatchSurface.
    Semantic topic expansion can be added later if accuracy data suggests
    it's needed.

    Scoring is based on match ratio: if the query has 3 terms and a chunk
    matches 2 topics, the score is 0.67.
    """

    name = "topic_match"

    def __init__(self) -> None:
        """Initialize TopicMatchSurface.

        No external dependencies required - uses direct SQLAlchemy queries.
        """
        pass

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
        """Search for chunks with topics matching query terms.

        Args:
            query_text: Original query text (unused by this surface).
            query_vector: Pre-computed embedding vector (unused by this surface).
            keyword_terms: Terms extracted from query via preprocess_query().
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of chunks to return.
            threshold: Minimum match ratio (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with chunk hits and execution time.

        """
        start = time.perf_counter()

        # Handle empty keyword_terms gracefully
        if not keyword_terms:
            logger.debug("TopicMatchSurface: no keyword_terms provided, returning empty")
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Keep original case for SQL query (PostgreSQL ?| is case-sensitive)
        # but use lowercase set for Python-side match ratio calculation
        query_terms_set = {t.lower() for t in keyword_terms}

        # Query chunks where topics array has any of the query terms
        # The ?| operator checks if JSONB array contains any of the given values
        # We query with original case to match how profiling stores topics
        stmt = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.topics,
            )
            .where(
                DocumentChunk.knowledge_base_id == str(kb_id),
                DocumentChunk.topics.isnot(None),
                DocumentChunk.topics.op("?|")(cast(keyword_terms, ARRAY(Text))),
            )
            .limit(limit * 3)  # Fetch extra to allow for threshold filtering
        )

        logger.debug(
            "TopicMatchSurface: searching",
            extra={"kb_id": str(kb_id), "query_terms": keyword_terms},
        )

        result = await db.execute(stmt)
        rows = result.fetchall()

        logger.debug(
            "TopicMatchSurface: query returned",
            extra={"row_count": len(rows), "query_terms": keyword_terms},
        )

        if not rows:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Calculate match ratio for each chunk
        scored_chunks: list[tuple[UUID, float, list[str]]] = []
        for chunk_id, _document_id, topics in rows:
            if not topics:
                continue

            # topics is a list of strings from JSONB
            chunk_topics_lower = {t.lower() for t in topics if isinstance(t, str)}
            matched = query_terms_set & chunk_topics_lower

            if not matched:
                continue

            # Score = proportion of query terms that matched
            score = len(matched) / len(query_terms_set)

            if score >= threshold:
                scored_chunks.append((chunk_id, score, sorted(matched)))

        # Sort by score descending and limit
        scored_chunks.sort(key=lambda x: x[1], reverse=True)
        scored_chunks = scored_chunks[:limit]

        # Build hits (convert string IDs to UUID for consistency with other surfaces)
        hits = [
            SurfaceHit(
                id=UUID(chunk_id) if isinstance(chunk_id, str) else chunk_id,
                id_type="chunk",
                score=score,
                metadata={"matched_terms": matched_terms},
            )
            for chunk_id, score, matched_terms in scored_chunks
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.debug(
            "TopicMatchSurface: returning hits",
            extra={"hit_count": len(hits), "elapsed_ms": round(elapsed_ms, 2)},
        )

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
