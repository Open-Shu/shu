"""BM25Surface - Postgres full-text search on document content.

Uses the ``search_vector`` tsvector column on the ``documents`` table with
``ts_rank`` scoring against ``plainto_tsquery``. This provides BM25-family
lexical retrieval at the document level with zero LLM cost, replacing the
previous KeywordMatchSurface.

The search_vector is populated by a Postgres trigger from title + content,
so no application-side population is needed.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import text as sa_text

from ....core.logging import get_logger
from ....models.document import Document
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Saturation constant for normalizing ts_rank into 0-1.
# score / (K + score) maps unbounded ts_rank to a 0-1 range with diminishing
# returns.  Calibrated against NFCorpus: with normalization=1, good matches
# typically score 0.05-0.13, so K=0.1 puts those in the 0.33-0.57 range —
# comparable to cosine-similarity scores from vector surfaces.
_SATURATION_K = 0.1


class BM25Surface(RetrievalSurface):
    """Retrieval surface using Postgres full-text search (ts_rank).

    Queries the ``search_vector`` tsvector column on documents using
    ``plainto_tsquery('english', query_text)`` and ranks results with
    ``ts_rank``. Returns document-level hits.

    This surface is effective for keyword-rich queries where exact lexical
    matching outperforms dense vector similarity.
    """

    name = "bm25"

    def __init__(self) -> None:
        """Initialize BM25Surface.

        No external dependencies required — uses direct SQLAlchemy queries
        against the documents table.
        """

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
        """Search documents using Postgres full-text search.

        Args:
            query_text: Original query text — used as input to plainto_tsquery.
            query_vector: Pre-computed embedding vector (unused by this surface).
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of documents to return.
            threshold: Minimum normalized score (after saturation).
            db: Async database session.

        Returns:
            SurfaceResult with document-level hits ranked by ts_rank.

        """
        start = time.perf_counter()

        if not query_text or not query_text.strip():
            logger.debug("BM25Surface: empty query_text, returning empty")
            elapsed_ms = (time.perf_counter() - start) * 1000
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Build tsquery from the plain query text
        tsquery = func.plainto_tsquery(sa_text("'english'"), query_text)

        # ts_rank with normalization=1 divides by 1+log(doc length) so long
        # documents don't dominate.
        rank = func.ts_rank(Document.search_vector, tsquery, 1)

        # We intentionally do NOT filter with `search_vector @@ tsquery` because
        # plainto_tsquery ANDs all terms — a single missing term eliminates the
        # entire document. Instead we filter on ts_rank > noise floor, which
        # allows partial-term matches to surface.
        noise_floor = 1e-10

        stmt = (
            select(Document.id, rank.label("rank"))
            .where(
                Document.knowledge_base_id == str(kb_id),
                Document.search_vector.isnot(None),
                rank > noise_floor,
            )
            .order_by(rank.desc())
            .limit(limit)
        )

        logger.debug(
            "BM25Surface: searching",
            extra={"kb_id": str(kb_id), "query_text_len": len(query_text)},
        )

        result = await db.execute(stmt)
        rows = result.fetchall()

        # Normalize via saturation: score / (K + score).  This maps unbounded
        # ts_rank values into 0-1 without depending on the result set, so a
        # single-document KB doesn't automatically score 1.0.
        hits = []
        for row in rows:
            raw = float(row.rank)
            normalized = raw / (_SATURATION_K + raw)
            if normalized >= threshold:
                hits.append(
                    SurfaceHit(
                        id=UUID(row.id) if isinstance(row.id, str) else row.id,
                        id_type="document",
                        score=normalized,
                        metadata={"raw_ts_rank": raw},
                    )
                )

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.debug(
            "BM25Surface: returning hits",
            extra={"hit_count": len(hits), "elapsed_ms": round(elapsed_ms, 2)},
        )

        return SurfaceResult(
            surface_name=self.name,
            hits=hits,
            execution_time_ms=elapsed_ms,
        )
