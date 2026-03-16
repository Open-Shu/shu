"""KeywordMatchSurface - JSONB matching on profiled chunk keywords.

Queries the keywords JSONB field on document_chunks to find chunks
containing specific terms extracted from the user's query. Uses match
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


class KeywordMatchSurface(RetrievalSurface):
    """Retrieval surface for JSONB keyword matching on chunks.

    This surface finds chunks whose profiled keywords overlap with keywords
    extracted from the user query. Particularly effective for factual queries
    containing named entities, technical terms, or specific identifiers.

    Scoring is based on match ratio: if the query has 3 keywords and a chunk
    matches 2, the score is 0.67. This rewards chunks matching more query terms.
    """

    name = "keyword_match"

    def __init__(self) -> None:
        """Initialize KeywordMatchSurface.

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
        """Search for chunks with keywords matching query terms.

        Args:
            query_text: Original query text (unused by this surface).
            query_vector: Pre-computed embedding vector (unused by this surface).
            keyword_terms: Keywords extracted from query via preprocess_query().
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
            logger.debug("KeywordMatchSurface: no keyword_terms provided, returning empty")
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Keywords are stored lowercase (normalized in DocumentChunk.set_profile).
        # Lowercase query terms here so the SQL ?| index match is case-insensitive.
        query_terms_lower = [t.lower() for t in keyword_terms]
        query_terms_set = set(query_terms_lower)

        # Query chunks where keywords array has any of the query terms.
        # Both sides are lowercase so the GIN index is fully utilized.
        # NOTE: No SQL LIMIT here - we fetch all matching chunks and score/limit in Python.
        # This ensures we don't arbitrarily exclude better-scoring chunks when many match.
        stmt = select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            DocumentChunk.keywords,
        ).where(
            DocumentChunk.knowledge_base_id == str(kb_id),
            DocumentChunk.keywords.isnot(None),
            DocumentChunk.keywords.op("?|")(cast(query_terms_lower, ARRAY(Text))),
        )

        logger.debug(
            "KeywordMatchSurface: searching",
            extra={"kb_id": str(kb_id), "query_terms": keyword_terms},
        )

        result = await db.execute(stmt)
        rows = result.fetchall()

        logger.debug(
            "KeywordMatchSurface: query returned",
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
        for chunk_id, _document_id, keywords in rows:
            if not keywords:
                continue

            # keywords are stored lowercase; lower() here is a safety net
            chunk_keywords_lower = {k.lower() for k in keywords if isinstance(k, str)}
            matched = query_terms_set & chunk_keywords_lower

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
            "KeywordMatchSurface: returning hits",
            extra={"hit_count": len(hits), "elapsed_ms": round(elapsed_ms, 2)},
        )

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
