"""KeywordMatchSurface - Document-level keyword coverage scoring.

Queries the keywords JSONB field on document_chunks to find chunks containing
specific terms extracted from the user's query, then aggregates matched keywords
at the document level. Score = len(unique_matched_terms_across_doc) / len(query_terms).

Query terms are expanded with plural/singular variants via inflect so that
"container" matches stored keyword "containers" and vice versa. Scoring uses
the original query term count as the denominator to avoid dilution.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

import inflect
from sqlalchemy import Text, cast as sa_cast, select
from sqlalchemy.dialects.postgresql import ARRAY

from ....core.logging import get_logger
from ....models.document import DocumentChunk
from ..protocol import RetrievalSurface, SurfaceHit, SurfaceResult

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Module-level inflect engine (stateless after init, safe to share)
_inflect_engine = inflect.engine()


def _expand_term(term: str) -> set[str]:
    """Return a term plus its plural and singular variants.

    Always includes the original. inflect.singular_noun() returns False
    when the word is already singular, so we guard against that.
    """
    variants = {term}
    try:
        plural = _inflect_engine.plural(term)  # type: ignore[arg-type]
        if plural:
            variants.add(plural)
        singular = _inflect_engine.singular_noun(term)  # type: ignore[arg-type]
        if singular and isinstance(singular, str):
            variants.add(singular)
    except Exception:
        pass
    return variants


class KeywordMatchSurface(RetrievalSurface):
    """Retrieval surface for document-level keyword coverage scoring.

    This surface finds chunks whose profiled keywords overlap with keywords
    extracted from the user query, then aggregates matches at the document
    level. Particularly effective for factual queries containing named entities,
    technical terms, or specific identifiers.

    Scoring is based on document-level coverage: keywords matched across all
    chunks of a document are unioned, then scored as
    len(unique_matched) / len(query_terms). A document with query keywords
    spread across N chunks scores identically to one with all keywords in a
    single chunk.

    Query terms are expanded with plural/singular variants so that
    morphological near-misses (e.g., "policy" vs "policies") still match.
    The scoring denominator is always the original query term count.
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
        """Search for documents with keywords matching query terms.

        Fetches all chunks with any matching keyword, groups by document,
        unions matched keywords per document, and scores by coverage ratio.

        Args:
            query_text: Original query text (unused by this surface).
            query_vector: Pre-computed embedding vector (unused by this surface).
            keyword_terms: Keywords extracted from query via preprocess_query().
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of documents to return.
            threshold: Minimum match ratio (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with document-level hits and execution time.

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

        # Build variant map: original term -> {original, plural, singular}.
        # The expanded set is used for the SQL ?| match (wider net), but scoring
        # counts original terms matched (denominator = len(query_terms_set)).
        term_variants: dict[str, set[str]] = {t: _expand_term(t) for t in query_terms_set}
        expanded_terms = sorted({v for variants in term_variants.values() for v in variants})

        # Query chunks where keywords array has any of the expanded query terms.
        # Both sides are lowercase so the GIN index is fully utilized.
        # NOTE: No SQL LIMIT here - we fetch all matching chunks and aggregate in Python.
        stmt = select(
            DocumentChunk.document_id,
            DocumentChunk.keywords,
        ).where(
            DocumentChunk.knowledge_base_id == str(kb_id),
            DocumentChunk.keywords.isnot(None),
            DocumentChunk.keywords.op("?|")(sa_cast(expanded_terms, ARRAY(Text))),
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

        # Aggregate matched keywords at the document level.
        # Each document gets the union of all matched keywords across its chunks.
        # Only retain keywords that are in the expanded set (variants of query terms).
        expanded_terms_set = set(expanded_terms)
        doc_keyword_hits: dict[str | UUID, set[str]] = defaultdict(set)
        for document_id, keywords in rows:
            if not keywords:
                continue

            # keywords are stored lowercase; lower() here is a safety net
            chunk_keywords_lower = {k.lower() for k in keywords if isinstance(k, str)}
            doc_keyword_hits[document_id] |= chunk_keywords_lower & expanded_terms_set

        # Score each document: count original query terms where any variant matched.
        # Denominator is always len(query_terms_set) — no dilution from expansion.
        scored_docs: list[tuple[str | UUID, float, list[str]]] = []
        for doc_id, doc_keywords in doc_keyword_hits.items():
            matched_original_terms = [
                term for term, variants in term_variants.items() if variants & doc_keywords
            ]

            if not matched_original_terms:
                continue

            score = len(matched_original_terms) / len(query_terms_set)

            if score >= threshold:
                scored_docs.append((doc_id, score, sorted(matched_original_terms)))

        # Sort by score descending and limit
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        scored_docs = scored_docs[:limit]

        # Build hits (convert string IDs to UUID for consistency with other surfaces)
        hits = [
            SurfaceHit(
                id=UUID(doc_id) if isinstance(doc_id, str) else doc_id,
                id_type="document",
                score=score,
                metadata={"matched_terms": matched_terms},
            )
            for doc_id, score, matched_terms in scored_docs
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
