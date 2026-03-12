"""Retrieval surface implementations.

Each surface wraps a different retrieval strategy (vector search, keyword, etc.)
and implements the RetrievalSurface protocol.
"""

from .chunk_vector import ChunkVectorSurface
from .synopsis_match import SynopsisMatchSurface

__all__ = [
    "ChunkVectorSurface",
    "SynopsisMatchSurface",
]
