"""Protocol and types for multi-surface retrieval.

Defines the RetrievalSurface protocol and associated dataclasses that
all retrieval surfaces must implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SurfaceHit:
    """A single hit from a retrieval surface.

    Attributes:
        id: The chunk_id or document_id depending on id_type.
        id_type: Whether this hit refers to a chunk or document.
        score: Normalized similarity score (0.0-1.0).
        metadata: Surface-specific metadata (e.g., matched text, position).

    """

    id: UUID
    id_type: Literal["chunk", "document"]
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SurfaceResult:
    """Results from a single retrieval surface.

    Attributes:
        surface_name: Name of the surface that produced these results.
        hits: List of hits sorted by score descending.
        execution_time_ms: Time taken to execute the search.

    """

    surface_name: str
    hits: list[SurfaceHit]
    execution_time_ms: float


@dataclass(frozen=True)
class ContributingChunk:
    """A chunk that contributed to a fused result.

    Attributes:
        chunk_id: The chunk's UUID.
        chunk_index: Position of the chunk within its document.
        surface: Name of the surface that found this chunk.
        score: The chunk's score from that surface.
        snippet: Text excerpt from the chunk.
        summary: Optional chunk summary if available.
        start_char: Start character offset in the source document.
        end_char: End character offset in the source document.

    """

    chunk_id: UUID
    chunk_index: int
    surface: str
    score: float
    snippet: str
    summary: str | None = None
    start_char: int | None = None
    end_char: int | None = None


@dataclass
class FusedResult:
    """A document-level result after score fusion across surfaces.

    Attributes:
        document_id: The document's UUID.
        document_title: Title of the document.
        final_score: Weighted combined score from all surfaces.
        surface_scores: Per-surface scores that contributed to final_score.
        contributing_chunks: Chunks from this document across all surfaces.
        file_type: Document file type (e.g., "pdf", "txt", "docx").
        source_url: URL of the source document if available.
        source_id: External source identifier if available.
        created_at: Document creation timestamp.

    """

    document_id: UUID
    document_title: str
    final_score: float
    surface_scores: dict[str, float]
    contributing_chunks: list[ContributingChunk]
    file_type: str = "txt"
    source_url: str | None = None
    source_id: str | None = None
    created_at: datetime | None = None


@runtime_checkable
class RetrievalSurface(Protocol):
    """Protocol for retrieval surfaces.

    Each surface implements a different retrieval strategy (vector similarity,
    keyword matching, synopsis matching, etc.) and returns normalized results
    that can be fused together.
    """

    name: str

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
        """Execute a search against this surface.

        Args:
            query_text: The original query text.
            query_vector: Pre-computed embedding vector for the query.
            keyword_terms: Extracted keyword terms for keyword-based surfaces.
            kb_id: Knowledge base ID to scope the search.
            limit: Maximum number of results to return.
            threshold: Minimum score threshold (0.0-1.0).
            db: Async database session.

        Returns:
            SurfaceResult with hits and execution time.

        """
        ...
