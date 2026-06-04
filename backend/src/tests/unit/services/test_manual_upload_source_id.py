"""Unit tests for manual_upload_source_id (SHU-817 dedup source_id derivation).

These cases catch normalization bugs (whitespace, unicode form, case sensitivity)
that integration tests — which upload one fixed filename — would not isolate.
"""

import unicodedata

from shu.services.ingestion_service import manual_upload_source_id


class TestManualUploadSourceId:
    """Stable, normalized per-KB source_id derivation for manual uploads."""

    def test_same_filename_is_stable(self) -> None:
        assert manual_upload_source_id("report.pdf") == manual_upload_source_id("report.pdf")

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert manual_upload_source_id("  report.pdf  ") == manual_upload_source_id("report.pdf")

    def test_unicode_nfc_and_nfd_collapse(self) -> None:
        # "café" composed (NFC) vs decomposed (NFD) are different byte strings...
        nfc = unicodedata.normalize("NFC", "café.pdf")
        nfd = unicodedata.normalize("NFD", "café.pdf")
        assert nfc != nfd
        # ...but normalize to the same document identity.
        assert manual_upload_source_id(nfc) == manual_upload_source_id(nfd)

    def test_case_is_preserved(self) -> None:
        # Different case = intentionally distinct documents (no casefolding).
        assert manual_upload_source_id("Report.pdf") != manual_upload_source_id("report.pdf")

    def test_different_names_differ(self) -> None:
        assert manual_upload_source_id("a.pdf") != manual_upload_source_id("b.pdf")

    def test_format_and_width(self) -> None:
        sid = manual_upload_source_id("report.pdf")
        assert sid.startswith("manual-upload-")
        assert len(sid) == len("manual-upload-") + 16

    def test_empty_and_whitespace_only_are_stable(self) -> None:
        assert manual_upload_source_id("") == manual_upload_source_id("   ")
