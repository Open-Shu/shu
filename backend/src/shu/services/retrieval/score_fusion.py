"""ScoreFusionService - Weighted score fusion across retrieval surfaces.

Aggregates results from multiple retrieval surfaces, groups by document,
applies weighted combination, and returns ranked FusedResults.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from ...core.logging import get_logger
from ...models.document import Document, DocumentChunk
from .protocol import ContributingChunk, FusedResult, SurfaceHit, SurfaceResult

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Default surface weights (can be overridden via config)
DEFAULT_SURFACE_WEIGHTS: dict[str, float] = {
    "chunk_vector": 0.25,
    "chunk_summary": 0.25,
    "query_match": 0.20,
    "synopsis_match": 0.15,
    "bm25": 0.0,
}

# Maximum snippet length for contributing chunks
MAX_SNIPPET_LENGTH = 200

# Supported fusion formulas
FUSION_FORMULA_MAX_SQRT = "max_sqrt_mean_max"
FUSION_FORMULA_WEIGHTED_AVG = "weighted_average"
DEFAULT_FUSION_FORMULA = FUSION_FORMULA_WEIGHTED_AVG


def _fuse_max_sqrt_mean_max(
    surface_scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Max x √(mean/max) fusion.

    The max score determines the ceiling. The mean/max ratio measures
    surface agreement using actual scores. Documents with balanced scores
    across surfaces get full credit; those dominated by a single surface
    are penalized proportionally.

    Best for: single-chunk corpora where surfaces produce correlated scores.
    """
    vector_scores = [score for name, score in surface_scores.items() if weights.get(name, 0) > 0]
    max_surface_score = max(vector_scores) if vector_scores else 0.0
    if max_surface_score > 0:
        mean_score = sum(vector_scores) / len(vector_scores)
        agreement = mean_score / max_surface_score
        return max_surface_score * math.sqrt(agreement)
    return 0.0


def _fuse_weighted_average(
    surface_scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Weighted average fusion.

    Each surface contributes proportionally to its configured weight.
    Allows genuine multi-surface consensus without penalizing documents
    found strongly by a single surface.

    Best for: multi-chunk corpora where surfaces find genuinely different
    content in different parts of the document.
    """
    weighted_sum = 0.0
    total_weight = 0.0
    for name, score in surface_scores.items():
        w = weights.get(name, 0)
        if w > 0 and score > 0:
            weighted_sum += score * w
            total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 0.0


_FUSION_FUNCTIONS = {
    FUSION_FORMULA_MAX_SQRT: _fuse_max_sqrt_mean_max,
    FUSION_FORMULA_WEIGHTED_AVG: _fuse_weighted_average,
}


def _ensure_uuid(val: UUID | str) -> UUID:
    """Convert a value to UUID, handling both string and UUID inputs.

    asyncpg returns UUID columns as uuid.UUID objects, while some test mocks
    return strings. This helper handles both cases safely.
    """
    return val if isinstance(val, UUID) else UUID(val)


class ScoreFusionService:
    """Service for fusing scores from multiple retrieval surfaces.

    Takes results from multiple surfaces, groups hits by document,
    applies weighted combination, and produces ranked FusedResults.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        fusion_formula: str = DEFAULT_FUSION_FORMULA,
    ) -> None:
        """Initialize with optional custom weights and fusion formula.

        Args:
            weights: Mapping of surface_name -> weight. If not provided,
                     uses DEFAULT_SURFACE_WEIGHTS.
            fusion_formula: Which fusion function to use. One of
                     "max_sqrt_mean_max" or "weighted_average".

        """
        self._weights = weights or DEFAULT_SURFACE_WEIGHTS
        if fusion_formula not in _FUSION_FUNCTIONS:
            raise ValueError(
                f"Unknown fusion formula '{fusion_formula}'. " f"Supported: {list(_FUSION_FUNCTIONS.keys())}"
            )
        self._fusion_formula = fusion_formula
        self._fuse_fn = _FUSION_FUNCTIONS[fusion_formula]

    async def fuse(  # noqa: PLR0912, PLR0915
        self,
        surface_results: list[SurfaceResult],
        *,
        query_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.0,
        db: AsyncSession,
    ) -> tuple[list[FusedResult], dict[str, dict[str, float]]]:
        """Fuse results from multiple surfaces into ranked document results.

        Args:
            surface_results: Results from each surface.
            query_type: Optional query type for future weight adjustments.
                Currently unused; reserved for query-type-specific weight
                overrides (e.g., factual vs interpretive queries).
            limit: Maximum number of documents to return.
            threshold: Minimum final score threshold.
            db: Async database session.

        Returns:
            Tuple of (fused_results, all_surface_scores) where fused_results
            is sorted by final_score descending and truncated to limit, and
            all_surface_scores is {doc_id_str: {surface: score}} for ALL
            scored documents before truncation.

        """
        # TODO: Use query_type to select weight overrides when implemented
        _ = query_type  # Unused for now
        if not surface_results:
            return [], {}

        # Step 1: Collect all chunk IDs that need document_id lookup
        chunk_ids_to_resolve: set[UUID] = set()
        for result in surface_results:
            for hit in result.hits:
                if hit.id_type == "chunk":
                    chunk_ids_to_resolve.add(hit.id)

        # Step 2: Resolve chunk_id -> document_id mapping
        chunk_to_doc: dict[UUID, UUID] = {}
        if chunk_ids_to_resolve:
            chunk_to_doc = await self._resolve_chunk_documents(list(chunk_ids_to_resolve), db)

        # Step 3: Group hits by document_id
        # doc_hits[document_id][surface_name] = list of (hit, chunk_id_or_none)
        doc_hits: dict[UUID, dict[str, list[SurfaceHit]]] = defaultdict(lambda: defaultdict(list))

        for result in surface_results:
            surface_name = result.surface_name
            for hit in result.hits:
                if hit.id_type == "document":
                    doc_id = hit.id
                else:
                    # Chunk hit - look up document
                    doc_id = chunk_to_doc.get(hit.id)
                    if doc_id is None:
                        logger.warning(
                            "Chunk not found in document mapping",
                            extra={"chunk_id": str(hit.id)},
                        )
                        continue

                doc_hits[doc_id][surface_name].append(hit)

        if not doc_hits:
            return [], {}

        # Step 4: Compute weighted scores per document
        doc_scores: dict[UUID, tuple[float, dict[str, float], dict[str, dict]]] = {}
        for doc_id, surface_hits in doc_hits.items():
            surface_scores: dict[str, float] = {}
            surface_metadata: dict[str, dict] = {}
            weighted_sum = 0.0
            total_weight = 0.0

            for surface_name, hits in surface_hits.items():
                weight = self._weights.get(surface_name, 0)

                # Use max score from this surface for this document
                best_hit = max(hits, key=lambda h: h.score)
                max_score = best_hit.score

                # Always record the score for visibility in results
                surface_scores[surface_name] = max_score

                # Only include in weighted sum if weight > 0
                if weight > 0:
                    weighted_sum += max_score * weight
                    total_weight += weight

                # Collect metadata from best-scoring hit (for document-level surfaces)
                if best_hit.metadata:
                    surface_metadata[surface_name] = best_hit.metadata

            # Skip documents with no valid surface contributions
            if total_weight == 0:
                continue

            # Fill in 0.0 for surfaces that didn't contribute to this document
            # This provides explicit visibility that a surface ran but found nothing
            for surface_name in self._weights:
                if surface_name not in surface_scores:
                    surface_scores[surface_name] = 0.0

            final_score = self._fuse_fn(surface_scores, self._weights)
            doc_scores[doc_id] = (final_score, surface_scores, surface_metadata)

        # Capture all surface scores before truncation — used by benchmarks
        # for unbiased per-surface evaluation. Built from doc_hits (not
        # doc_scores) so zero-weight-only documents (e.g. BM25-only when
        # BM25 weight is 0) are still included for per-surface analysis.
        all_surface_scores: dict[str, dict[str, float]] = {}
        for doc_id, surface_hits in doc_hits.items():
            scores_for_doc: dict[str, float] = {}
            for surface_name, hits in surface_hits.items():
                scores_for_doc[surface_name] = max(h.score for h in hits)
            all_surface_scores[str(doc_id)] = scores_for_doc

        # Step 5: Filter by threshold and sort
        filtered_docs = [
            (doc_id, score, surface_scores, surface_metadata)
            for doc_id, (score, surface_scores, surface_metadata) in doc_scores.items()
            if score >= threshold
        ]
        filtered_docs.sort(key=lambda x: x[1], reverse=True)
        top_docs = filtered_docs[:limit]

        if not top_docs:
            return [], all_surface_scores

        # Step 6: Load document metadata and chunk details
        doc_ids = [d[0] for d in top_docs]
        doc_metadata = await self._load_document_metadata(doc_ids, db)

        # Collect all chunk IDs for contributing chunks
        all_chunk_ids: set[UUID] = set()
        for doc_id, surface_hits in doc_hits.items():
            if doc_id in doc_ids:
                for _, hits in surface_hits.items():
                    for hit in hits:
                        if hit.id_type == "chunk":
                            all_chunk_ids.add(hit.id)

        chunk_details = await self._load_chunk_details(list(all_chunk_ids), db)

        # Step 7: Build FusedResults
        results: list[FusedResult] = []
        for doc_id, final_score, surface_scores, surface_metadata in top_docs:
            contributing_chunks: list[ContributingChunk] = []

            # Collect contributing chunks from all surfaces
            for surface_name, hits in doc_hits[doc_id].items():
                for hit in hits:
                    if hit.id_type == "chunk":
                        details = chunk_details.get(hit.id)
                        if details:
                            chunk_index, content, summary, start_char, end_char, chunk_meta = details
                            snippet = self._make_snippet(content)
                            contributing_chunks.append(
                                ContributingChunk(
                                    chunk_id=hit.id,
                                    chunk_index=chunk_index,
                                    surface=surface_name,
                                    score=hit.score,
                                    snippet=snippet,
                                    content=content,
                                    summary=summary,
                                    start_char=start_char,
                                    end_char=end_char,
                                    matched_query=hit.metadata.get("matched_query"),
                                    chunk_metadata=chunk_meta,
                                )
                            )

            # Sort contributing chunks by score descending
            contributing_chunks.sort(key=lambda c: c.score, reverse=True)

            # Get document metadata (title, file_type, source_url, source_id, created_at, synopsis)
            title, file_type, source_url, source_id, created_at, synopsis = doc_metadata.get(
                doc_id, ("Unknown", "txt", None, None, None, None)
            )

            results.append(
                FusedResult(
                    document_id=doc_id,
                    document_title=title,
                    final_score=final_score,
                    surface_scores=surface_scores,
                    contributing_chunks=contributing_chunks,
                    surface_metadata=surface_metadata,
                    file_type=file_type,
                    source_url=source_url,
                    source_id=source_id,
                    created_at=created_at,
                    synopsis=synopsis,
                )
            )

        return results, all_surface_scores

    async def _resolve_chunk_documents(self, chunk_ids: list[UUID], db: AsyncSession) -> dict[UUID, UUID]:
        """Look up document_id for each chunk_id.

        Args:
            chunk_ids: List of chunk IDs to resolve.
            db: Async database session.

        Returns:
            Mapping of chunk_id -> document_id.

        """
        if not chunk_ids:
            return {}

        stmt = select(DocumentChunk.id, DocumentChunk.document_id).where(
            DocumentChunk.id.in_([str(cid) for cid in chunk_ids])
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {_ensure_uuid(row[0]): _ensure_uuid(row[1]) for row in rows}

    async def _load_document_metadata(
        self, doc_ids: list[UUID], db: AsyncSession
    ) -> dict[UUID, tuple[str, str, str | None, str | None, datetime | None, str | None]]:
        """Load document metadata for a list of document IDs.

        Args:
            doc_ids: List of document IDs.
            db: Async database session.

        Returns:
            Mapping of document_id -> (title, file_type, source_url, source_id, created_at, synopsis).

        """
        if not doc_ids:
            return {}

        stmt = select(
            Document.id,
            Document.title,
            Document.file_type,
            Document.source_url,
            Document.source_id,
            Document.created_at,
            Document.synopsis,
        ).where(Document.id.in_([str(did) for did in doc_ids]))
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {_ensure_uuid(row[0]): (row[1], row[2] or "txt", row[3], row[4], row[5], row[6]) for row in rows}

    async def _load_chunk_details(
        self, chunk_ids: list[UUID], db: AsyncSession
    ) -> dict[UUID, tuple[int, str, str | None, int | None, int | None, dict | None]]:
        """Load chunk details for contributing chunks.

        Args:
            chunk_ids: List of chunk IDs.
            db: Async database session.

        Returns:
            Mapping of chunk_id -> (chunk_index, content, summary, start_char, end_char, chunk_metadata).

        """
        if not chunk_ids:
            return {}

        stmt = select(
            DocumentChunk.id,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            DocumentChunk.summary,
            DocumentChunk.start_char,
            DocumentChunk.end_char,
            DocumentChunk.chunk_metadata,
        ).where(DocumentChunk.id.in_([str(cid) for cid in chunk_ids]))
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {_ensure_uuid(row[0]): (row[1], row[2], row[3], row[4], row[5], row[6]) for row in rows}

    def _make_snippet(self, content: str) -> str:
        """Create a snippet from chunk content.

        Args:
            content: Full chunk content.

        Returns:
            Truncated snippet with ellipsis if needed.

        """
        if len(content) <= MAX_SNIPPET_LENGTH:
            return content
        return content[: MAX_SNIPPET_LENGTH - 3] + "..."
