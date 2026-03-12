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
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value[:max_length]
    return value.strip("-")
