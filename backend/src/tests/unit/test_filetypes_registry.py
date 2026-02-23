"""Tests for the central file-type registry (SHU-318)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shu.ingestion.filetypes import (
    ALL_BINARY_SIGNATURES,
    DEFAULT_KB_FILE_TYPES,
    EXT_TO_INGESTION_TYPE,
    KNOWN_BINARY_EXTENSIONS,
    MAGIC_BYTES,
    MIME_TO_EXT,
    SUPPORTED_TEXT_EXTENSIONS,
    FileTypeEntry,
    IngestionType,
    _REGISTRY,
    detect_extension_from_bytes,
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
            ("text/rtf", ".rtf"),
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

    @pytest.mark.parametrize(
        ("mime_with_params", "expected"),
        [
            ("text/plain; charset=utf-8", ".txt"),
            ("application/pdf; name=report.pdf", ".pdf"),
            ("text/html; charset=iso-8859-1", ".html"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document; charset=utf-8", ".docx"),
        ],
    )
    def test_mime_with_parameters_stripped(self, mime_with_params: str, expected: str) -> None:
        """MIME parameters after ';' must be stripped before lookup."""
        assert normalize_extension(mime_with_params) == expected


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
# IngestionType and registry structure
# ---------------------------------------------------------------------------


class TestIngestionType:
    def test_all_members_are_str(self) -> None:
        """IngestionType members should be str-comparable."""
        for member in IngestionType:
            assert isinstance(member, str)
            assert member == member.value

    def test_ext_to_ingestion_type_covers_supported(self) -> None:
        """Every SUPPORTED_TEXT_EXTENSIONS entry must have an IngestionType mapping."""
        for ext in SUPPORTED_TEXT_EXTENSIONS:
            assert ext in EXT_TO_INGESTION_TYPE, f"{ext} missing from EXT_TO_INGESTION_TYPE"

    def test_ext_to_ingestion_type_values_are_valid(self) -> None:
        for ext, itype in EXT_TO_INGESTION_TYPE.items():
            assert isinstance(itype, IngestionType), f"EXT_TO_INGESTION_TYPE[{ext!r}] is not an IngestionType"


class TestFileTypeEntryStructure:
    def test_registry_entries_all_have_dotted_extensions(self) -> None:
        for entry in _REGISTRY:
            assert entry.extension.startswith("."), f"{entry.extension!r} should start with '.'"

    def test_plugin_only_entries(self) -> None:
        """Only .email should be plugin_only."""
        plugin_only = {e.extension for e in _REGISTRY if e.plugin_only}
        assert plugin_only == {".email"}

    def test_registry_length_matches_supported(self) -> None:
        """_REGISTRY must have exactly one entry per supported extension."""
        assert len(_REGISTRY) == len(SUPPORTED_TEXT_EXTENSIONS)

    def test_no_duplicate_extensions(self) -> None:
        exts = [e.extension for e in _REGISTRY]
        assert len(exts) == len(set(exts)), "Duplicate extensions in _REGISTRY"

    def test_entries_are_frozen(self) -> None:
        entry = _REGISTRY[0]
        assert isinstance(entry, FileTypeEntry)
        with pytest.raises(AttributeError):
            entry.extension = ".foo"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Magic bytes
# ---------------------------------------------------------------------------


class TestMagicBytes:
    def test_magic_bytes_keys_are_dotted(self) -> None:
        for ext in MAGIC_BYTES:
            assert ext.startswith("."), f"MAGIC_BYTES key {ext!r} should start with '.'"

    def test_magic_bytes_includes_xlsx_pptx(self) -> None:
        """xlsx/pptx have no extraction handler but need upload validation."""
        assert ".xlsx" in MAGIC_BYTES
        assert ".pptx" in MAGIC_BYTES

    def test_all_binary_signatures_non_empty(self) -> None:
        assert len(ALL_BINARY_SIGNATURES) > 0

    def test_all_binary_signatures_contains_pdf(self) -> None:
        assert b"\x25\x50\x44\x46" in ALL_BINARY_SIGNATURES

    def test_all_binary_signatures_contains_zip(self) -> None:
        assert b"\x50\x4b\x03\x04" in ALL_BINARY_SIGNATURES


# ---------------------------------------------------------------------------
# Known binary extensions
# ---------------------------------------------------------------------------


class TestKnownBinaryExtensions:
    def test_all_dotted(self) -> None:
        for ext in KNOWN_BINARY_EXTENSIONS:
            assert ext.startswith("."), f"{ext!r} in KNOWN_BINARY_EXTENSIONS should start with '.'"

    def test_superset_of_magic_bytes_keys(self) -> None:
        """Every extension with magic bytes should also be in KNOWN_BINARY_EXTENSIONS."""
        for ext in MAGIC_BYTES:
            assert ext in KNOWN_BINARY_EXTENSIONS, f"{ext} has MAGIC_BYTES but is not in KNOWN_BINARY_EXTENSIONS"

    def test_includes_common_binary_formats(self) -> None:
        for ext in (".exe", ".dll", ".zip", ".iso", ".dmg"):
            assert ext in KNOWN_BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# Derived constant integrity
# ---------------------------------------------------------------------------


class TestRegistryConstants:
    def test_default_kb_file_types_derived_from_supported(self) -> None:
        """DEFAULT_KB_FILE_TYPES must be derived from SUPPORTED_TEXT_EXTENSIONS
        (minus plugin-only pseudo-extensions like .email)."""
        from shu.ingestion.filetypes import _PLUGIN_ONLY_EXTENSIONS

        expected = sorted(
            ext.lstrip(".") for ext in SUPPORTED_TEXT_EXTENSIONS if ext not in _PLUGIN_ONLY_EXTENSIONS
        )
        assert expected == DEFAULT_KB_FILE_TYPES

    def test_default_kb_file_types_excludes_plugin_only(self) -> None:
        """Plugin-only extensions like .email must not appear in upload defaults."""
        assert "email" not in DEFAULT_KB_FILE_TYPES

    def test_default_kb_file_types_no_xlsx_pptx_without_handler(self) -> None:
        """xlsx/pptx have no TextExtractor handler, so they must not be declared."""
        assert ".xlsx" not in SUPPORTED_TEXT_EXTENSIONS
        assert ".pptx" not in SUPPORTED_TEXT_EXTENSIONS
        assert "xlsx" not in DEFAULT_KB_FILE_TYPES
        assert "pptx" not in DEFAULT_KB_FILE_TYPES

    def test_supported_extensions_superset_of_defaults(self) -> None:
        """Every type in DEFAULT_KB_FILE_TYPES should have a dotted entry in SUPPORTED_TEXT_EXTENSIONS."""
        for ft in DEFAULT_KB_FILE_TYPES:
            assert f".{ft}" in SUPPORTED_TEXT_EXTENSIONS, f".{ft} missing from SUPPORTED_TEXT_EXTENSIONS"

    def test_text_extractor_uses_registry(self) -> None:
        """TextExtractor.supported_extensions must equal SUPPORTED_TEXT_EXTENSIONS."""
        from shu.processors.text_extractor import TextExtractor

        mock_settings = MagicMock()
        mock_settings.ocr_render_scale = 2.0
        mock_settings.ocr_page_timeout = 60
        mock_settings.ocr_max_concurrent_jobs = 1
        config_manager = MagicMock()
        config_manager.settings = mock_settings

        extractor = TextExtractor(config_manager=config_manager)
        assert extractor.supported_extensions == set(SUPPORTED_TEXT_EXTENSIONS)

    def test_every_ingestion_type_has_handler(self) -> None:
        """Every IngestionType used in the registry (except PDF) must have a
        handler in TextExtractor._type_handlers."""
        from shu.processors.text_extractor import TextExtractor

        mock_settings = MagicMock()
        mock_settings.ocr_render_scale = 2.0
        mock_settings.ocr_page_timeout = 60
        mock_settings.ocr_max_concurrent_jobs = 1
        config_manager = MagicMock()
        config_manager.settings = mock_settings

        extractor = TextExtractor(config_manager=config_manager)
        handled_types = set(extractor._type_handlers.keys()) | {IngestionType.PDF}

        for itype in set(EXT_TO_INGESTION_TYPE.values()):
            assert itype in handled_types, (
                f"IngestionType.{itype.name} is used in the registry but has no handler in TextExtractor"
            )

    def test_mime_to_ext_all_values_are_dotted(self) -> None:
        for mime, ext in MIME_TO_EXT.items():
            assert ext.startswith("."), f"MIME_TO_EXT[{mime!r}] = {ext!r} should start with '.'"

    def test_supported_extensions_all_dotted(self) -> None:
        for ext in SUPPORTED_TEXT_EXTENSIONS:
            assert ext.startswith("."), f"{ext!r} in SUPPORTED_TEXT_EXTENSIONS should start with '.'"


# ---------------------------------------------------------------------------
# detect_extension_from_bytes
# ---------------------------------------------------------------------------


class TestDetectExtensionFromBytes:
    def test_pdf_header_returns_pdf(self) -> None:
        assert detect_extension_from_bytes(b"%PDF-1.4 content") == ".pdf"

    def test_ole2_header_returns_doc(self) -> None:
        ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        assert detect_extension_from_bytes(ole2) == ".doc"

    def test_zip_header_returns_none(self) -> None:
        """ZIP is ambiguous (docx/xlsx/pptx) — must return None."""
        zip_bytes = b"\x50\x4b\x03\x04" + b"\x00" * 100
        assert detect_extension_from_bytes(zip_bytes) is None

    def test_short_data_returns_none(self) -> None:
        assert detect_extension_from_bytes(b"\x25\x50") is None

    def test_empty_data_returns_none(self) -> None:
        assert detect_extension_from_bytes(b"") is None

    def test_plain_text_returns_none(self) -> None:
        assert detect_extension_from_bytes(b"Hello, world!") is None
