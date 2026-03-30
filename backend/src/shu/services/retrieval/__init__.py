"""Multi-surface retrieval services.

This package implements multi-surface search with parallel execution
and score fusion across multiple retrieval strategies.
"""

from .multi_surface_search import MultiSurfaceSearchService
from .protocol import (
    ContributingChunk,
    FusedResult,
    RetrievalSurface,
    SurfaceHit,
    SurfaceResult,
)
from .result_formatter import FormattedChunk, FormattedDocument, dedupe_contributing_chunks, format_results
from .score_fusion import ScoreFusionService

__all__ = [
    "ContributingChunk",
    "dedupe_contributing_chunks",
    "FormattedChunk",
    "FormattedDocument",
    "FusedResult",
    "MultiSurfaceSearchService",
    "RetrievalSurface",
    "ScoreFusionService",
    "SurfaceHit",
    "SurfaceResult",
    "format_results",
]
