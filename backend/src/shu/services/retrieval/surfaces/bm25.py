"""BM25Surface - True Okapi BM25 search via ParadeDB pg_search.

Uses ParadeDB's ``|||`` (disjunctive OR) operator and ``pdb.score()`` for
real BM25 scoring with IDF weighting and term frequency saturation. This
replaces the previous ts_rank implementation which lacked IDF.

Requires the pg_search extension and a BM25 index on the documents table:

    CREATE INDEX ix_documents_bm25 ON documents
    USING bm25 (
        id,
        (title::pdb.simple('stemmer=english', 'stopwords_language=english')),
        (content::pdb.simple('stemmer=english', 'stopwords_language=english'))
    )
    WITH (key_field='id');
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text as sa_text

from ....core.logging import get_logger
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Saturation constant for normalizing BM25 scores into 0-1.
# score / (K + score) maps unbounded BM25 to a 0-1 range.
# Calibrated against NFCorpus with ParadeDB: good matches score 5-15,
# median ~3, strong matches 10+. K=10 puts strong matches at 0.5-0.6,
# comparable to cosine similarity from vector surfaces.
_SATURATION_K = 10.0


class BM25Surface(RetrievalSurface):
    """Retrieval surface using ParadeDB BM25 full-text search.

    Queries the documents table using ParadeDB's disjunctive (OR) operator
    ``|||`` on title and content columns, scored by ``pdb.score()``. Returns
    document-level hits with true Okapi BM25 scores.
    """

    name = "bm25"

    def __init__(self) -> None:
        """Initialize BM25Surface.

        No external dependencies required — uses direct SQL queries against
        the ParadeDB BM25 index on the documents table.
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
        """Search documents using ParadeDB BM25.

        Args:
            query_text: Original query text — searched via disjunctive OR.
            query_vector: Pre-computed embedding vector (unused by this surface).
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of documents to return.
            threshold: Minimum normalized score (after saturation).
            db: Async database session.

        Returns:
            SurfaceResult with document-level hits ranked by BM25 score.

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

        # ParadeDB ||| operator does disjunctive (OR) matching — partial
        # term matches work natively, unlike plainto_tsquery which ANDs all
        # terms. pdb.score(id) returns the real Okapi BM25 score.
        stmt = sa_text("""
            SELECT id, pdb.score(id) AS bm25_score
            FROM documents
            WHERE (title ||| :query OR content ||| :query)
              AND knowledge_base_id = :kb_id
            ORDER BY pdb.score(id) DESC
            LIMIT :limit
        """)

        logger.debug(
            "BM25Surface: searching",
            extra={"kb_id": str(kb_id), "query_text_len": len(query_text)},
        )

        try:
            result = await db.execute(
                stmt,
                {"query": query_text, "kb_id": str(kb_id), "limit": limit},
            )
            rows = result.fetchall()
        except Exception as exc:
            # pg_search extension not installed or BM25 index missing —
            # degrade gracefully so multi-surface search continues without BM25.
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "BM25Surface: query failed (pg_search extension may not be installed)",
                extra={"error": str(exc), "elapsed_ms": round(elapsed_ms, 2)},
            )
            return SurfaceResult(
                surface_name=self.name,
                hits=[],
                execution_time_ms=elapsed_ms,
            )

        # Normalize via saturation: score / (K + score). BM25 scores are
        # unbounded (grow with more matching terms). K=10 maps strong
        # matches (score 10-15) to 0.5-0.6, comparable to cosine similarity.
        hits = []
        for row in rows:
            raw = float(row.bm25_score)
            normalized = raw / (_SATURATION_K + raw)
            if normalized >= threshold:
                hits.append(
                    SurfaceHit(
                        id=UUID(row.id) if isinstance(row.id, str) else row.id,
                        id_type="document",
                        score=normalized,
                        metadata={"raw_bm25": raw},
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
