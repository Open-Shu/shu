"""Tests for the central file-type registry (SHU-318)."""

from __future__ import annotations

import pytest

from shu.ingestion.filetypes import (
    DEFAULT_KB_FILE_TYPES,
    MIME_TO_EXT,
    SUPPORTED_TEXT_EXTENSIONS,
    normalize_extension,
)

# ---------------------------------------------------------------------------
# normalize_extension — filename precedence
# ---------------------------------------------------------------------------


class TestNormalizeExtensionFilenames:
    """normalize_extension should prefer the filename suffix."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            ("report.pdf", ".pdf"),
            ("doc.docx", ".docx"),
            ("legacy.doc", ".doc"),
            ("readme.txt", ".txt"),
            ("notes.md", ".md"),
            ("formatted.rtf", ".rtf"),
            ("page.html", ".html"),
            ("page.htm", ".htm"),
            ("data.csv", ".csv"),
            ("script.py", ".py"),
            ("app.js", ".js"),
            ("sheet.xlsx", ".xlsx"),
            ("deck.pptx", ".pptx"),
            ("mail.eml", ".eml"),
            # Case-insensitive
            ("REPORT.PDF", ".pdf"),
            ("Notes.MD", ".md"),
            ("report.DOCX", ".docx"),
            # Nested path
            ("/path/to/report.pdf", ".pdf"),
            ("some/dir/readme.txt", ".txt"),
            # Multiple dots
            ("my.file.txt", ".txt"),
            ("archive.tar.gz", ".gz"),
        ],
    )
    def test_filename_extraction(self, input_val: str, expected: str) -> None:
        assert normalize_extension(input_val) == expected


# ---------------------------------------------------------------------------
# normalize_extension — MIME fallback
# ---------------------------------------------------------------------------


class TestNormalizeExtensionMime:
    """normalize_extension should fall back to MIME lookup when no filename suffix."""

    @pytest.mark.parametrize(
        ("mime", "expected"),
        [
            ("application/pdf", ".pdf"),
            ("application/msword", ".doc"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
            ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
            ("application/rtf", ".rtf"),
            ("text/plain", ".txt"),
            ("text/markdown", ".md"),
            ("text/html", ".html"),
            ("text/csv", ".csv"),
            ("text/javascript", ".js"),
            ("application/javascript", ".js"),
            ("application/x-javascript", ".js"),
            ("text/ecmascript", ".js"),
            ("application/ecmascript", ".js"),
            ("text/x-python", ".py"),
            ("application/x-python", ".py"),
            ("application/x-python-code", ".py"),
            ("message/rfc822", ".eml"),
        ],
    )
    def test_mime_lookup(self, mime: str, expected: str) -> None:
        assert normalize_extension(mime) == expected


# ---------------------------------------------------------------------------
# normalize_extension — edge cases
# ---------------------------------------------------------------------------


class TestNormalizeExtensionEdgeCases:
    def test_empty_string_returns_bin(self) -> None:
        assert normalize_extension("") == ".bin"

    def test_none_returns_bin(self) -> None:
        assert normalize_extension(None) == ".bin"  # type: ignore[arg-type]

    def test_whitespace_only_returns_bin(self) -> None:
        assert normalize_extension("   ") == ".bin"

    def test_unknown_mime_returns_bin(self) -> None:
        assert normalize_extension("application/x-totally-unknown-format") == ".bin"

    def test_filename_beats_ambiguity(self) -> None:
        """A filename with a dot should use the suffix, not treat it as MIME."""
        assert normalize_extension("data.csv") == ".csv"

    def test_bare_word_no_dot_returns_bin(self) -> None:
        """A string with no dot and not in MIME map should return .bin."""
        assert normalize_extension("randomgarbage") == ".bin"


# ---------------------------------------------------------------------------
# _infer_file_type — behavioral parity with the old implementation
# ---------------------------------------------------------------------------


class TestInferFileType:
    """Verify _infer_file_type produces correct results via the registry."""

    @pytest.mark.parametrize(
        ("filename", "mime_type", "expected"),
        [
            ("report.pdf", "", "pdf"),
            ("", "application/pdf", "pdf"),
            ("data.csv", "text/csv", "csv"),
            ("readme.txt", "", "txt"),
            ("", "text/plain", "txt"),
            ("", "text/markdown", "md"),
            ("", "text/html", "html"),
            ("script.py", "", "py"),
            ("", "text/x-python", "py"),
            ("", "application/x-python", "py"),
            ("app.js", "", "js"),
            ("", "text/javascript", "js"),
            ("", "application/javascript", "js"),
            ("doc.docx", "", "docx"),
            ("mail.eml", "", "eml"),
            # Unknown MIME, no extension → fallback to "txt"
            ("", "application/octet-stream", "txt"),
            # Google Docs title (no extension), unknown MIME → "txt"
            ("My Google Doc", "application/vnd.google-apps.document", "txt"),
            # Filename takes precedence over MIME
            ("notes.md", "text/plain", "md"),
        ],
    )
    def test_infer_file_type(self, filename: str, mime_type: str, expected: str) -> None:
        from shu.services.ingestion_service import _infer_file_type

        assert _infer_file_type(filename, mime_type) == expected


# ---------------------------------------------------------------------------
# Constant integrity
# ---------------------------------------------------------------------------


class TestRegistryConstants:
    def test_default_kb_file_types_content(self) -> None:
        expected = ["pdf", "docx", "doc", "txt", "md", "rtf", "html", "htm", "csv", "py", "js", "xlsx", "pptx"]
        assert expected == DEFAULT_KB_FILE_TYPES

    def test_supported_extensions_superset_of_defaults(self) -> None:
        """Every type in DEFAULT_KB_FILE_TYPES should have a dotted entry in SUPPORTED_TEXT_EXTENSIONS."""
        for ft in DEFAULT_KB_FILE_TYPES:
            assert f".{ft}" in SUPPORTED_TEXT_EXTENSIONS, f".{ft} missing from SUPPORTED_TEXT_EXTENSIONS"

    def test_mime_to_ext_all_values_are_dotted(self) -> None:
        for mime, ext in MIME_TO_EXT.items():
            assert ext.startswith("."), f"MIME_TO_EXT[{mime!r}] = {ext!r} should start with '.'"

    def test_supported_extensions_all_dotted(self) -> None:
        for ext in SUPPORTED_TEXT_EXTENSIONS:
            assert ext.startswith("."), f"{ext!r} in SUPPORTED_TEXT_EXTENSIONS should start with '.'"
