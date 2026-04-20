"""Defensive Decimal coercion for untrusted external values.

Provider APIs return cost fields in varying shapes: numeric, stringified
number, sometimes `None` or the literal `"N/A"` when a cost is unavailable.
`safe_decimal` coerces whatever comes in to a `Decimal`, falling back to
`Decimal(0)` and logging a warning for anything it can't parse.
"""

from decimal import Decimal
from typing import Any

from .logging import get_logger

logger = get_logger(__name__)


def safe_decimal(value: Any) -> Decimal:
    """Convert a value to Decimal, falling back to zero for None or non-numeric."""
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        logger.warning("Malformed decimal value, defaulting to 0: %r", value)
        return Decimal(0)
