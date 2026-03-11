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
from .score_fusion import ScoreFusionService

__all__ = [
    "ContributingChunk",
    "FusedResult",
    "MultiSurfaceSearchService",
    "RetrievalSurface",
    "ScoreFusionService",
    "SurfaceHit",
    "SurfaceResult",
]
