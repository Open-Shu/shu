"""Retrieval surface implementations.

Each surface wraps a different retrieval strategy (vector search, BM25, etc.)
and implements the RetrievalSurface protocol.
"""

from .bm25 import BM25Surface
from .chunk_summary_vector import ChunkSummaryVectorSurface
from .chunk_vector import ChunkVectorSurface
from .query_match import QueryMatchSurface
from .synopsis_match import SynopsisMatchSurface
from .topic_match import TopicMatchSurface

__all__ = [
    "BM25Surface",
    "ChunkSummaryVectorSurface",
    "ChunkVectorSurface",
    "QueryMatchSurface",
    "SynopsisMatchSurface",
    "TopicMatchSurface",
]
