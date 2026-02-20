"""Central file-type registry for the Shu ingestion pipeline.

This module is the single source of truth for supported file extensions,
MIME-to-extension mappings, and default KB upload types within backend
services.  Plugins are intentionally kept independent and should NOT
import from this module.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# MIME → dotted extension  (curated — covers all types handled by the
# ingestion pipeline today)
# ---------------------------------------------------------------------------
MIME_TO_EXT: dict[str, str] = {
    # Documents
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/rtf": ".rtf",
    # Plain text / markup
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/html": ".html",
    "text/csv": ".csv",
    # JavaScript (5 MIME variants in the wild)
    "text/javascript": ".js",
    "application/javascript": ".js",
    "application/x-javascript": ".js",
    "text/ecmascript": ".js",
    "application/ecmascript": ".js",
    # Python (3 MIME variants)
    "text/x-python": ".py",
    "application/x-python": ".py",
    "application/x-python-code": ".py",
    # Email
    "message/rfc822": ".eml",
}

# ---------------------------------------------------------------------------
# All extensions the backend can extract text from (with leading dots).
# ---------------------------------------------------------------------------
SUPPORTED_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
        ".md",
        ".rtf",
        ".html",
        ".htm",
        ".csv",
        ".py",
        ".js",
        ".xlsx",
        ".pptx",
        ".email",  # Gmail plugin pseudo-extension
        ".eml",
    }
)

# ---------------------------------------------------------------------------
# Default allowed types for KB uploads (without dots).  Referenced by
# config.py Settings.kb_upload_allowed_types default_factory.
# ---------------------------------------------------------------------------
DEFAULT_KB_FILE_TYPES: list[str] = [
    "pdf",
    "docx",
    "doc",
    "txt",
    "md",
    "rtf",
    "html",
    "htm",
    "csv",
    "py",
    "js",
    "xlsx",
    "pptx",
]


def normalize_extension(name_or_mime: str) -> str:
    """Return a dotted extension like ``".pdf"`` for a filename or MIME type.

    Resolution order:
    1. Check the curated :data:`MIME_TO_EXT` map (handles dotted MIME subtypes
       like OOXML correctly).
    2. If the value looks like a MIME type (single ``/``, not an absolute path),
       use :func:`mimetypes.guess_extension`.
    3. If the value looks like a filename (contains a dot), extract the suffix.
    4. Return ``".bin"`` when nothing matches.
    """
    value = (name_or_mime or "").strip()
    if not value:
        return ".bin"

    lower = value.lower()

    # 1. Curated MIME map — checked first so dotted subtypes like
    #    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    #    resolve correctly instead of being misinterpreted as filenames.
    if lower in MIME_TO_EXT:
        return MIME_TO_EXT[lower]

    # 2. MIME-type heuristic: exactly one "/" and not an absolute path
    #    (e.g. "application/pdf", "application/vnd.google-apps.document").
    #    Go straight to stdlib to avoid PurePosixPath extracting a bogus
    #    suffix from dotted subtypes.
    if value.count("/") == 1 and not value.startswith("/"):
        guessed = mimetypes.guess_extension(lower, strict=False)
        if guessed:
            return guessed.lower()
        return ".bin"

    # 3. Try as a filename — extract suffix via PurePosixPath.
    if "." in value:
        ext = PurePosixPath(value).suffix.lower()
        if ext:
            return ext

    # 4. stdlib fallback for bare words
    guessed = mimetypes.guess_extension(lower, strict=False)
    if guessed:
        return guessed.lower()

    return ".bin"
