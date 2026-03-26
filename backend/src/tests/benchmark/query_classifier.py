"""Heuristic query type classification for benchmark analysis.

Classifies queries into broad types (factual, interpretive, structural)
based on query structure. Used for per-query-type metric breakdowns.
"""

from __future__ import annotations

import re
from enum import Enum


class QueryType(str, Enum):
    """Broad query type categories for analysis."""

    FACTUAL = "factual"
    INTERPRETIVE = "interpretive"
    STRUCTURAL = "structural"
    UNKNOWN = "unknown"


# Patterns checked in order — first match wins
_STRUCTURAL_PATTERNS = [
    r"difference between",
    r"compared? to",
    r"relationship between",
    r"how does .+ relate to",
    r"versus|vs\.?",
    r"contrast .+ (with|and)",
    r"similarities? (between|of|and)",
]

_INTERPRETIVE_PREFIXES = (
    "how does",
    "how do",
    "how can",
    "how is",
    "how are",
    "why ",
    "explain",
    "what is the role",
    "what is the effect",
    "what is the mechanism",
    "what is the impact",
    "what causes",
    "what leads to",
    "what are the consequences",
)

_FACTUAL_PREFIXES = (
    "what is",
    "what are",
    "which",
    "how many",
    "how much",
    "when ",
    "where ",
    "who ",
    "name ",
    "list ",
    "define ",
    "what does",
    "what was",
    "what were",
)


def classify_query(text: str) -> QueryType:
    """Classify a query into a broad type based on heuristics.

    This is a best-effort classifier for grouping queries in the
    surface contribution matrix. Not intended for routing decisions.

    Args:
        text: Query text.

    Returns:
        QueryType classification.
    """
    text_lower = text.lower().strip()

    # Structural: comparisons, relationships, contrasts
    for pattern in _STRUCTURAL_PATTERNS:
        if re.search(pattern, text_lower):
            return QueryType.STRUCTURAL

    # Interpretive: explanations, mechanisms, causes
    if text_lower.startswith(_INTERPRETIVE_PREFIXES):
        return QueryType.INTERPRETIVE

    # Factual: specific facts, definitions, quantities
    if text_lower.startswith(_FACTUAL_PREFIXES):
        return QueryType.FACTUAL

    return QueryType.UNKNOWN
