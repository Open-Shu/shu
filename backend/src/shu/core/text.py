"""Text utilities."""

import re
import unicodedata


def slugify(value: str, *, max_length: int = 100) -> str:
    """Convert a string to a URL-friendly slug.

    Normalizes unicode, lowercases, replaces non-alphanumeric characters with
    hyphens, and collapses consecutive hyphens.  The result is truncated to
    *max_length* characters (trailing hyphens are stripped after truncation).

    >>> slugify("Morning Briefing")
    'morning-briefing'
    >>> slugify("Inbox Triage (v2)")
    'inbox-triage-v2'
    """
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        raise ValueError("max_length must be a positive integer")

    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value[:max_length]
    return value.strip("-")
