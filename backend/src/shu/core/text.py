"""Text utilities."""

import re
import unicodedata


def slugify(value: str) -> str:
    """Convert a string to a URL-friendly slug.

    Normalizes unicode, lowercases, replaces non-alphanumeric characters with
    hyphens, and collapses consecutive hyphens.

    >>> slugify("Morning Briefing")
    'morning-briefing'
    >>> slugify("Inbox Triage (v2)")
    'inbox-triage-v2'
    """
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")
