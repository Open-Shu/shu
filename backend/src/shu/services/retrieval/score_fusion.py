"""ScoreFusionService - Weighted score fusion across retrieval surfaces.

Aggregates results from multiple retrieval surfaces, groups by document,
applies weighted combination, and returns ranked FusedResults.
"""

from __future__ import annotations

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
    "chunk_vector": 0.30,
    "query_match": 0.25,
    "synopsis_match": 0.20,
    # Future surfaces (PR 3+):
    # "keyword_match": 0.15,
    # "topic_match": 0.10,
}

# Maximum snippet length for contributing chunks
MAX_SNIPPET_LENGTH = 200


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

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        """Initialize with optional custom weights.

        Args:
            weights: Mapping of surface_name -> weight. If not provided,
                     uses DEFAULT_SURFACE_WEIGHTS.

        """
        self._weights = weights or DEFAULT_SURFACE_WEIGHTS

    async def fuse(  # noqa: PLR0912, PLR0915
        self,
        surface_results: list[SurfaceResult],
        *,
        query_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.0,
        db: AsyncSession,
    ) -> list[FusedResult]:
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
            List of FusedResult sorted by final_score descending.

        """
        # TODO: Use query_type to select weight overrides when implemented
        _ = query_type  # Unused for now
        if not surface_results:
            return []

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
            return []

        # Step 4: Compute weighted scores per document
        doc_scores: dict[UUID, tuple[float, dict[str, float], dict[str, dict]]] = {}
        for doc_id, surface_hits in doc_hits.items():
            surface_scores: dict[str, float] = {}
            surface_metadata: dict[str, dict] = {}
            weighted_sum = 0.0
            total_weight = 0.0

            for surface_name, hits in surface_hits.items():
                weight = self._weights.get(surface_name, 0.1)
                if weight <= 0:
                    # Skip surfaces with non-positive weights
                    continue

                # Use max score from this surface for this document
                best_hit = max(hits, key=lambda h: h.score)
                max_score = best_hit.score
                surface_scores[surface_name] = max_score
                weighted_sum += max_score * weight
                total_weight += weight

                # Collect metadata from best-scoring hit (for document-level surfaces)
                if best_hit.metadata:
                    surface_metadata[surface_name] = best_hit.metadata

            # Skip documents with no valid surface contributions
            if total_weight == 0:
                continue

            # Normalize by total weight used
            final_score = weighted_sum / total_weight
            doc_scores[doc_id] = (final_score, surface_scores, surface_metadata)

        # Step 5: Filter by threshold and sort
        filtered_docs = [
            (doc_id, score, surface_scores, surface_metadata)
            for doc_id, (score, surface_scores, surface_metadata) in doc_scores.items()
            if score >= threshold
        ]
        filtered_docs.sort(key=lambda x: x[1], reverse=True)
        top_docs = filtered_docs[:limit]

        if not top_docs:
            return []

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
                            chunk_index, content, summary, start_char, end_char = details
                            snippet = self._make_snippet(content)
                            contributing_chunks.append(
                                ContributingChunk(
                                    chunk_id=hit.id,
                                    chunk_index=chunk_index,
                                    surface=surface_name,
                                    score=hit.score,
                                    snippet=snippet,
                                    summary=summary,
                                    start_char=start_char,
                                    end_char=end_char,
                                )
                            )

            # Sort contributing chunks by score descending
            contributing_chunks.sort(key=lambda c: c.score, reverse=True)

            # Get document metadata (title, file_type, source_url, source_id, created_at)
            title, file_type, source_url, source_id, created_at = doc_metadata.get(
                doc_id, ("Unknown", "txt", None, None, None)
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
                )
            )

        return results

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
    ) -> dict[UUID, tuple[str, str, str | None, str | None, datetime | None]]:
        """Load document metadata for a list of document IDs.

        Args:
            doc_ids: List of document IDs.
            db: Async database session.

        Returns:
            Mapping of document_id -> (title, file_type, source_url, source_id, created_at).

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
        ).where(Document.id.in_([str(did) for did in doc_ids]))
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {_ensure_uuid(row[0]): (row[1], row[2] or "txt", row[3], row[4], row[5]) for row in rows}

    async def _load_chunk_details(
        self, chunk_ids: list[UUID], db: AsyncSession
    ) -> dict[UUID, tuple[int, str, str | None, int | None, int | None]]:
        """Load chunk details for contributing chunks.

        Args:
            chunk_ids: List of chunk IDs.
            db: Async database session.

        Returns:
            Mapping of chunk_id -> (chunk_index, content, summary, start_char, end_char).

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
        ).where(DocumentChunk.id.in_([str(cid) for cid in chunk_ids]))
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {_ensure_uuid(row[0]): (row[1], row[2], row[3], row[4], row[5]) for row in rows}

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
