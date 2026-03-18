"""Constants for query service.

Re-exports stop word sets from query_constants for backward compatibility.
"""

from ..query_constants import COMPREHENSIVE_STOP_WORDS, TITLE_MATCH_STOP_WORDS

__all__ = ["COMPREHENSIVE_STOP_WORDS", "TITLE_MATCH_STOP_WORDS"]
