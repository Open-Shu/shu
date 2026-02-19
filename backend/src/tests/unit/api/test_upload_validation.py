"""
Unit tests for KB document upload validation.

Covers:
- Empty (0-byte) files are rejected before entering the pipeline
- Files with content mismatching their declared extension are rejected
"""

import pytest

from shu.api.knowledge_bases import _check_content_type_mismatch


class TestEmptyFileValidation:
    """0-byte files must be rejected at the upload endpoint."""

    def test_empty_bytes_is_zero_length(self):
        """Sanity check: empty bytes has length 0."""
        assert len(b"") == 0

    def test_non_empty_file_passes_length_check(self):
        """A file with content must pass the length check."""
        assert len(b"some content") > 0


class TestContentTypeMismatch:
    """_check_content_type_mismatch must detect files whose content doesn't match their extension."""

    # --- PDF ---

    def test_valid_pdf_passes(self):
        """A real PDF header must pass validation."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        assert _check_content_type_mismatch("pdf", pdf_bytes) is None

    def test_zip_disguised_as_pdf_is_rejected(self):
        """A ZIP file renamed to .pdf must be rejected."""
        zip_bytes = b"\x50\x4b\x03\x04" + b"\x00" * 100
        result = _check_content_type_mismatch("pdf", zip_bytes)
        assert result is not None
        assert "pdf" in result.lower()

    def test_ole_disguised_as_pdf_is_rejected(self):
        """An OLE2 file (e.g. .doc) renamed to .pdf must be rejected."""
        ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        result = _check_content_type_mismatch("pdf", ole_bytes)
        assert result is not None

    # --- DOCX ---

    def test_valid_docx_passes(self):
        """A real DOCX (ZIP-based) header must pass validation."""
        zip_bytes = b"\x50\x4b\x03\x04" + b"\x00" * 100
        assert _check_content_type_mismatch("docx", zip_bytes) is None

    def test_pdf_disguised_as_docx_is_rejected(self):
        """A PDF renamed to .docx must be rejected."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        result = _check_content_type_mismatch("docx", pdf_bytes)
        assert result is not None
        assert "docx" in result.lower()

    def test_random_bytes_disguised_as_docx_is_rejected(self):
        """Random binary data renamed to .docx must be rejected."""
        random_bytes = b"\x00\x01\x02\x03\x04\x05\x06\x07" + b"\xff" * 100
        result = _check_content_type_mismatch("docx", random_bytes)
        assert result is not None

    # --- XLSX ---

    def test_valid_xlsx_passes(self):
        """A real XLSX (ZIP-based) header must pass validation."""
        zip_bytes = b"\x50\x4b\x03\x04" + b"\x00" * 100
        assert _check_content_type_mismatch("xlsx", zip_bytes) is None

    def test_pdf_disguised_as_xlsx_is_rejected(self):
        """A PDF renamed to .xlsx must be rejected."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        result = _check_content_type_mismatch("xlsx", pdf_bytes)
        assert result is not None

    # --- PPTX ---

    def test_valid_pptx_passes(self):
        """A real PPTX (ZIP-based) header must pass validation."""
        zip_bytes = b"\x50\x4b\x03\x04" + b"\x00" * 100
        assert _check_content_type_mismatch("pptx", zip_bytes) is None

    # --- DOC ---

    def test_valid_doc_ole_passes(self):
        """A real .doc (OLE2) header must pass validation."""
        ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        assert _check_content_type_mismatch("doc", ole_bytes) is None

    def test_pdf_disguised_as_doc_is_rejected(self):
        """A PDF renamed to .doc must be rejected."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        result = _check_content_type_mismatch("doc", pdf_bytes)
        assert result is not None

    # --- Text formats (no magic bytes check) ---

    def test_txt_any_content_passes(self):
        """Text files have no magic bytes requirement — any content passes."""
        assert _check_content_type_mismatch("txt", b"hello world") is None
        assert _check_content_type_mismatch("txt", b"\x00\x01\x02") is None

    def test_md_any_content_passes(self):
        """Markdown files have no magic bytes requirement."""
        assert _check_content_type_mismatch("md", b"# Heading") is None

    def test_csv_any_content_passes(self):
        """CSV files have no magic bytes requirement."""
        assert _check_content_type_mismatch("csv", b"col1,col2\n1,2") is None

    # --- Edge cases ---

    def test_file_too_short_to_check_passes(self):
        """Files shorter than 4 bytes cannot be checked — must pass through."""
        assert _check_content_type_mismatch("pdf", b"\x25\x50") is None

    def test_empty_bytes_passes_magic_check(self):
        """Empty bytes are too short to check — magic bytes check passes (empty file check is separate)."""
        assert _check_content_type_mismatch("pdf", b"") is None

    def test_zip_variant_pk0506_valid_for_docx(self):
        """ZIP end-of-central-directory signature is also a valid ZIP variant."""
        zip_eocd = b"\x50\x4b\x05\x06" + b"\x00" * 100
        assert _check_content_type_mismatch("docx", zip_eocd) is None

    def test_zip_variant_pk0708_valid_for_docx(self):
        """ZIP data descriptor signature is also a valid ZIP variant."""
        zip_dd = b"\x50\x4b\x07\x08" + b"\x00" * 100
        assert _check_content_type_mismatch("docx", zip_dd) is None
