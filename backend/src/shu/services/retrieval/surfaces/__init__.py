"""Retrieval surface implementations.

Each surface wraps a different retrieval strategy (vector search, keyword, etc.)
and implements the RetrievalSurface protocol.
"""

from .chunk_vector import ChunkVectorSurface
from .keyword_match import KeywordMatchSurface
from .query_match import QueryMatchSurface
from .synopsis_match import SynopsisMatchSurface
from .topic_match import TopicMatchSurface

__all__ = [
    "ChunkVectorSurface",
    "KeywordMatchSurface",
    "QueryMatchSurface",
    "SynopsisMatchSurface",
    "TopicMatchSurface",
]
