"""Structured Multi-Surface Result Formatter (SHU-652).

Transforms fused multi-surface search results into structured, LLM-readable
document context. This is the single source of truth for how multi-surface
retrieval results are presented to any consumer — case study export, chat
RAG context, and automated evaluation.

Key responsibilities:
- Always include document synopsis (not just when synopsis_match fired)
- Deduplicate contributing chunks (same chunk from multiple surfaces → one entry)
- Annotate chunks with surface-specific context (matched_query, summary)
- Promote best content chunk when only title chunks matched a document
- Filter out title chunks from content output (their value is in scoring)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from ...core.logging import get_logger
from ...models.document import DocumentChunk

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shu.core.vector_store import VectorStore

    from .protocol import FusedResult

logger = get_logger(__name__)


@dataclass
class FormattedChunk:
    """A deduplicated, annotated chunk for structured output."""

    chunk_id: str
    chunk_index: int
    score: float
    content: str
    surfaces: list[str] = field(default_factory=list)
    summary: str | None = None
    matched_query: str | None = None
    promoted: bool = False


@dataclass
class FormattedDocument:
    """Structured document context for LLM consumption."""

    document_id: str
    document_title: str
    final_score: float
    surface_scores: dict[str, float]
    synopsis: str | None = None
    title_summary: str | None = None
    chunks: list[FormattedChunk] = field(default_factory=list)


def _is_title_chunk(chunk) -> bool:
    """Check if a contributing chunk is a title chunk."""
    meta = chunk.chunk_metadata or {}
    return meta.get("chunk_type") == "title"


def _dedupe_and_annotate(
    contributing_chunks: list,
) -> tuple[list[FormattedChunk], str | None]:
    """Deduplicate chunks by chunk_id, merge annotations from all surfaces.

    Keeps the highest score and collects all surface names, summaries,
    and matched queries from duplicate entries. Extracts the title chunk's
    summary for document-level display before filtering title chunks out.

    Returns:
        Tuple of (formatted_chunks, title_summary).

    """
    seen: dict[str, FormattedChunk] = {}
    title_summary: str | None = None

    for chunk in contributing_chunks:
        if _is_title_chunk(chunk):
            # Extract title chunk summary for document-level display
            if chunk.summary and not title_summary:
                title_summary = chunk.summary
            continue

        key = str(chunk.chunk_id)
        if key not in seen:
            # Only include summary when chunk_summary surface matched this chunk
            summary = chunk.summary if chunk.surface == "chunk_summary" else None
            seen[key] = FormattedChunk(
                chunk_id=key,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                content=chunk.content or chunk.snippet or "",
                surfaces=[chunk.surface],
                summary=summary,
                matched_query=chunk.matched_query,
            )
        else:
            existing = seen[key]
            existing.surfaces.append(chunk.surface)
            existing.score = max(existing.score, chunk.score)
            # Only merge summary from chunk_summary surface
            if chunk.summary and chunk.surface == "chunk_summary" and not existing.summary:
                existing.summary = chunk.summary
            if chunk.matched_query and not existing.matched_query:
                existing.matched_query = chunk.matched_query

    # Sort by score descending
    return sorted(seen.values(), key=lambda c: c.score, reverse=True), title_summary


async def _promote_best_chunk(
    doc_id: UUID,
    query_vector: list[float],
    vector_store: VectorStore,
    db: AsyncSession,
) -> FormattedChunk | None:
    """Fetch the best non-title content chunk for a document via scoped vector search.

    Used when a document's only contributing chunks are title chunks —
    the document was found through ingestion-time intelligence (query_match,
    synopsis_match) but no content chunks matched directly. Promoting the
    best content chunk delivers on that identification.
    """
    results = await vector_store.search(
        collection="chunks",
        query_vector=query_vector,
        db=db,
        limit=1,
        threshold=0.0,
        filters={"document_id": str(doc_id)},
        extra_where="(chunk_metadata->>'chunk_type' != 'title' OR chunk_metadata->>'chunk_type' IS NULL)",
    )

    if not results:
        return None

    # Load full content for the promoted chunk
    hit = results[0]
    stmt = select(
        DocumentChunk.chunk_index,
        DocumentChunk.content,
        DocumentChunk.summary,
    ).where(DocumentChunk.id == hit.id)
    row = (await db.execute(stmt)).first()

    if not row:
        return None

    return FormattedChunk(
        chunk_id=hit.id,
        chunk_index=row[0],
        score=hit.score,
        content=row[1] or "",
        surfaces=["promoted"],
        promoted=True,
    )


async def format_results(
    fused_results: list[FusedResult],
    query_vector: list[float],
    vector_store: VectorStore,
    db: AsyncSession,
) -> list[FormattedDocument]:
    """Transform fused results into structured document context.

    Args:
        fused_results: Results from score fusion.
        query_vector: Original query embedding (for promotion searches).
        vector_store: Vector store for promotion chunk lookups.
        db: Database session.

    Returns:
        List of FormattedDocument with deduplicated, annotated chunks.

    """
    formatted: list[FormattedDocument] = []

    for result in fused_results:
        # Deduplicate and annotate chunks, filtering out title chunks
        chunks, title_summary = _dedupe_and_annotate(result.contributing_chunks)

        # Check if we need to promote: document has contributing chunks
        # but ALL of them were title chunks (so chunks list is empty after filtering)
        has_contributing = len(result.contributing_chunks) > 0
        all_title = has_contributing and len(chunks) == 0

        if all_title:
            promoted = await _promote_best_chunk(result.document_id, query_vector, vector_store, db)
            if promoted:
                chunks = [promoted]
                logger.info(
                    "Promoted best content chunk for title-only document",
                    extra={
                        "document_id": str(result.document_id),
                        "document_title": result.document_title,
                        "promoted_score": promoted.score,
                    },
                )

        doc = FormattedDocument(
            document_id=str(result.document_id),
            document_title=result.document_title,
            final_score=result.final_score,
            surface_scores=result.surface_scores,
            synopsis=result.synopsis,
            title_summary=title_summary,
            chunks=chunks,
        )
        formatted.append(doc)

    return formatted
