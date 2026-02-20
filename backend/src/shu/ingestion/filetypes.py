"""Central file-type registry for the Shu ingestion pipeline.

This module is the **single source of truth** for:

* Supported file extensions and their ingestion handler categories
* MIME-to-extension mappings
* Default KB upload types
* Magic-byte signatures for content validation
* Known binary extensions (unsupported for text extraction)

Plugins are intentionally kept independent and should NOT import from
this module.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# IngestionType — the handler category each extension maps to
# ---------------------------------------------------------------------------


class IngestionType(str, Enum):
    """Handler category for text extraction dispatch.

    Each supported file extension maps to exactly one IngestionType.
    TextExtractor maps each IngestionType to a handler method.
    """

    PLAIN_TEXT = "plain_text"
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    RTF = "rtf"
    HTML = "html"
    EMAIL = "email"


# ---------------------------------------------------------------------------
# FileTypeEntry — one row in the registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileTypeEntry:
    """A single supported file type in the ingestion registry."""

    extension: str  # Dotted, lowercase — e.g. ".pdf"
    ingestion_type: IngestionType
    plugin_only: bool = False  # True = not available for direct user upload


# ---------------------------------------------------------------------------
# _REGISTRY — the canonical list.  Everything else is derived from this.
# ---------------------------------------------------------------------------

_REGISTRY: tuple[FileTypeEntry, ...] = (
    FileTypeEntry(".pdf", IngestionType.PDF),
    FileTypeEntry(".docx", IngestionType.DOCX),
    FileTypeEntry(".doc", IngestionType.DOC),
    FileTypeEntry(".txt", IngestionType.PLAIN_TEXT),
    FileTypeEntry(".md", IngestionType.PLAIN_TEXT),
    FileTypeEntry(".csv", IngestionType.PLAIN_TEXT),
    FileTypeEntry(".py", IngestionType.PLAIN_TEXT),
    FileTypeEntry(".js", IngestionType.PLAIN_TEXT),
    FileTypeEntry(".rtf", IngestionType.RTF),
    FileTypeEntry(".html", IngestionType.HTML),
    FileTypeEntry(".htm", IngestionType.HTML),
    FileTypeEntry(".email", IngestionType.EMAIL, plugin_only=True),
    FileTypeEntry(".eml", IngestionType.EMAIL),
)

# ---------------------------------------------------------------------------
# Derived constants — do NOT maintain these by hand; edit _REGISTRY instead.
# ---------------------------------------------------------------------------

SUPPORTED_TEXT_EXTENSIONS: frozenset[str] = frozenset(e.extension for e in _REGISTRY)

EXT_TO_INGESTION_TYPE: dict[str, IngestionType] = {e.extension: e.ingestion_type for e in _REGISTRY}

_PLUGIN_ONLY_EXTENSIONS: frozenset[str] = frozenset(e.extension for e in _REGISTRY if e.plugin_only)

DEFAULT_KB_FILE_TYPES: list[str] = sorted(e.extension.lstrip(".") for e in _REGISTRY if not e.plugin_only)

# ---------------------------------------------------------------------------
# MIME → dotted extension  (curated — covers all types handled by the
# ingestion pipeline today)
# ---------------------------------------------------------------------------

MIME_TO_EXT: dict[str, str] = {
    # Documents
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    # .xlsx/.pptx: recognized for MIME resolution and upload magic-byte validation,
    # but NOT extractable (no handler in _REGISTRY / TextExtractor).
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
# Magic-byte signatures — used for upload content validation
# (_check_content_type_mismatch) and fallback binary detection
# (_extract_text_fallback).
#
# Note: .xlsx and .pptx are included here even though they have no
# extraction handler (and are absent from _REGISTRY).  They are
# "recognized but not extractable" — we can validate their magic bytes
# at upload time to reject misnamed files early, but we cannot extract
# text from them.
# ---------------------------------------------------------------------------

_ZIP_SIGNATURES: tuple[bytes, ...] = (
    b"\x50\x4b\x03\x04",
    b"\x50\x4b\x05\x06",
    b"\x50\x4b\x07\x08",
)
_PDF_SIGNATURE: tuple[bytes, ...] = (b"\x25\x50\x44\x46",)  # %PDF
_OLE2_SIGNATURE: tuple[bytes, ...] = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)  # OLE2 compound document

MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    ".pdf": _PDF_SIGNATURE,
    ".docx": _ZIP_SIGNATURES,
    ".xlsx": _ZIP_SIGNATURES,
    ".pptx": _ZIP_SIGNATURES,
    ".doc": _OLE2_SIGNATURE,
}

ALL_BINARY_SIGNATURES: tuple[bytes, ...] = tuple({sig for sigs in MAGIC_BYTES.values() for sig in sigs})

# ---------------------------------------------------------------------------
# Known binary extensions — files that must never be decoded as raw text
# in the fallback extractor.
# ---------------------------------------------------------------------------

KNOWN_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".xlsx",
        ".zip",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".dat",
        ".obj",
        ".class",
        ".jar",
        ".war",
        ".ear",
        ".apk",
        ".ipa",
        ".dmg",
        ".iso",
        ".img",
        ".vhd",
        ".vmdk",
    }
)


# ---------------------------------------------------------------------------
# normalize_extension — filename / MIME → dotted extension
# ---------------------------------------------------------------------------


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


def detect_extension_from_bytes(data: bytes) -> str | None:
    """Attempt to identify a file's extension from its leading magic bytes.

    Only returns a result for unambiguous signatures:

    * PDF (``%PDF``) → ``".pdf"``
    * OLE2 compound document → ``".doc"`` (most common OLE2 format)

    ZIP-based formats (``.docx``, ``.xlsx``, ``.pptx``) share the same
    ``PK`` header and cannot be reliably distinguished without inspecting
    archive contents, so this function returns ``None`` for them.
    """
    if len(data) < 4:
        return None

    header = data[:8]

    for sig in _PDF_SIGNATURE:
        if header[: len(sig)] == sig:
            return ".pdf"

    for sig in _OLE2_SIGNATURE:
        if header[: len(sig)] == sig:
            return ".doc"

    return None
