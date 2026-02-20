"""Edge-case tests for TextExtractor, OcrCapability, and AttachmentService (SHU-574).

Covers:
- Config injection (no global singleton fallback)
- Per-page OCR edge cases in _process_pdf_with_ocr_direct
- OcrCapability integration (parameter mapping, audit logging)
- AttachmentService._fast_extract_text error handling
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extractor(
    ocr_render_scale: float = 2.0,
    ocr_page_timeout: int = 60,
    ocr_max_concurrent_jobs: int = 1,
):
    """Build a TextExtractor with a mock ConfigurationManager via real __init__."""
    from shu.processors.text_extractor import TextExtractor

    mock_settings = MagicMock()
    mock_settings.ocr_render_scale = ocr_render_scale
    mock_settings.ocr_page_timeout = ocr_page_timeout
    mock_settings.ocr_max_concurrent_jobs = ocr_max_concurrent_jobs

    config_manager = MagicMock()
    config_manager.settings = mock_settings

    extractor = TextExtractor(config_manager=config_manager)
    return extractor, config_manager


def _make_extractor_raw(
    ocr_render_scale: float = 2.0,
    ocr_page_timeout: int = 60,
    ocr_max_concurrent_jobs: int = 1,
):
    """Build a TextExtractor via __new__ (bypasses __init__) for internal-method tests."""
    from shu.processors.text_extractor import TextExtractor

    mock_settings = MagicMock()
    mock_settings.ocr_render_scale = ocr_render_scale
    mock_settings.ocr_page_timeout = ocr_page_timeout
    mock_settings.ocr_max_concurrent_jobs = ocr_max_concurrent_jobs

    config_manager = MagicMock()
    config_manager.settings = mock_settings

    extractor = TextExtractor.__new__(TextExtractor)
    extractor.config_manager = config_manager
    extractor._current_sync_job_id = None
    extractor._last_ocr_engine = None
    return extractor, config_manager


def _mock_fitz_doc(num_pages: int = 1):
    """Create a mock fitz.Document with *num_pages* mock pages."""
    pages = []
    for _ in range(num_pages):
        page = MagicMock()
        pix = MagicMock()
        pix.tobytes.return_value = _png_bytes()
        page.get_pixmap.return_value = pix
        pages.append(page)

    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=num_pages)
    doc.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
    return doc


def _png_bytes() -> bytes:
    """Return a tiny valid PNG image as bytes."""
    import io

    import numpy as np
    from PIL import Image

    arr = np.zeros((2, 2, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _load_ocr_capability_module():
    """Import the ocr_capability module, working around the host package circular import."""
    import importlib
    import sys
    import types

    host_pkg = "shu.plugins.host"
    if host_pkg not in sys.modules:
        stub = types.ModuleType(host_pkg)
        stub.__path__ = [
            str(Path(__file__).resolve().parents[3] / "shu" / "plugins" / "host")
        ]
        stub.__package__ = host_pkg
        sys.modules[host_pkg] = stub
        import shu.plugins

        shu.plugins.host = stub  # type: ignore[attr-defined]

    return importlib.import_module("shu.plugins.host.ocr_capability")


# ===========================================================================
# 1. Config injection
# ===========================================================================


class TestConfigInjection:
    """Verify TextExtractor uses the injected config_manager, not a global."""

    def test_uses_passed_config_manager(self):
        extractor, config_manager = _make_extractor()
        assert extractor.config_manager is config_manager

    @pytest.mark.asyncio
    async def test_per_kb_ocr_mode_honored(self):
        """ocr_mode='never' should result in use_ocr=False in the internal call."""
        extractor, _ = _make_extractor()

        with patch.object(
            extractor,
            "_extract_text_direct",
            new=AsyncMock(return_value=("text", False, None)),
        ) as mock_direct:
            await extractor.extract_text(
                file_path="test.pdf",
                file_bytes=b"fake",
                ocr_mode="never",
            )
            call_args = mock_direct.call_args
            # use_ocr is the 4th positional arg (after file_path, file_content, progress_context)
            assert call_args[0][3] is False  # use_ocr
            assert call_args[0][5] == "never"  # ocr_mode


# ===========================================================================
# 2. Per-page OCR edge cases
# ===========================================================================


class TestPerPageOcrEdgeCases:
    """Exercise _process_pdf_with_ocr_direct with mock fitz.Document objects."""

    @pytest.mark.asyncio
    async def test_ocr_returns_none_result(self):
        """OCR returning None for a page should not crash."""
        extractor, _ = _make_extractor_raw()
        doc = _mock_fitz_doc(1)

        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = None

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch("shu.processors.text_extractor.fitz", create=True),
        ):
            text, method, confidence = await extractor._process_pdf_with_ocr_direct(doc, "test.pdf")

        assert isinstance(text, str)
        assert method == "ocr"

    @pytest.mark.asyncio
    async def test_ocr_returns_empty_list(self):
        """OCR returning [] for a page should produce no text."""
        extractor, _ = _make_extractor_raw()
        doc = _mock_fitz_doc(1)

        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = []

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch("shu.processors.text_extractor.fitz", create=True),
        ):
            text, method, confidence = await extractor._process_pdf_with_ocr_direct(doc, "test.pdf")

        assert text.strip() == ""
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_short_detection_tuples_skipped(self):
        """Detection tuples with < 3 elements should be skipped (no crash)."""
        extractor, _ = _make_extractor_raw()
        doc = _mock_fitz_doc(1)

        mock_ocr = MagicMock()
        mock_ocr.readtext.return_value = [([0, 0, 10, 10], "text")]

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch("shu.processors.text_extractor.fitz", create=True),
        ):
            text, method, confidence = await extractor._process_pdf_with_ocr_direct(doc, "test.pdf")

        # The detection is skipped because len(detection) < 3
        assert "text" not in text
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_exception_in_page_ocr_marks_page_failed(self):
        """If OCR fails on page 1, page 2 should still be processed."""
        extractor, _ = _make_extractor_raw()
        doc = _mock_fitz_doc(2)

        call_count = 0

        def readtext_side_effect(_img):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("OCR engine crashed")
            return [([0, 0, 10, 10], "page2text", 0.95)]

        mock_ocr = MagicMock()
        mock_ocr.readtext.side_effect = readtext_side_effect

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch("shu.processors.text_extractor.fitz", create=True),
        ):
            text, method, confidence = await extractor._process_pdf_with_ocr_direct(doc, "test.pdf")

        assert "[OCR failed on page 1]" in text
        assert "page2text" in text

    @pytest.mark.asyncio
    async def test_timeout_on_single_page_continues_remaining(self):
        """Timeout on page 1 should not prevent page 2 from being processed."""
        extractor, _ = _make_extractor_raw(ocr_page_timeout=1)
        doc = _mock_fitz_doc(2)

        call_count = 0

        def readtext_side_effect(_img):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                import time

                time.sleep(5)
                return []
            return [([0, 0, 10, 10], "page2text", 0.90)]

        mock_ocr = MagicMock()
        mock_ocr.readtext.side_effect = readtext_side_effect

        with (
            patch.object(extractor, "get_ocr_instance", new=AsyncMock(return_value=mock_ocr)),
            patch("shu.processors.text_extractor.fitz", create=True),
        ):
            text, method, confidence = await extractor._process_pdf_with_ocr_direct(doc, "test.pdf")

        assert "page2text" in text


# ===========================================================================
# 3. OcrCapability integration
# ===========================================================================

# Module loaded once via helper to avoid circular import in host.__init__.
_ocr_mod = _load_ocr_capability_module()


class TestOcrCapabilityIntegration:
    """Verify OcrCapability correctly delegates to TextExtractor.

    OcrCapability lives inside shu.plugins.host, whose ``__init__`` triggers a
    deep circular import chain.  The module is pre-loaded at module level via
    ``_load_ocr_capability_module()`` with the host package stubbed.
    """

    def _make_capability(self, ocr_mode: str | None = None):
        config_manager = MagicMock()
        return _ocr_mod.OcrCapability(
            plugin_name="test-plugin",
            user_id="user-123",
            config_manager=config_manager,
            ocr_mode=ocr_mode,
        )

    @pytest.mark.asyncio
    async def test_file_bytes_and_mime_reach_extractor(self):
        cap = self._make_capability()

        mock_result = {"text": "extracted", "metadata": {"method": "ocr"}}
        with patch.object(_ocr_mod, "TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(return_value=mock_result)

            result = await cap.extract_text(
                file_bytes=b"pdf-data",
                mime_type="application/pdf",
                mode="fallback",
            )

        instance.extract_text.assert_called_once_with(
            file_bytes=b"pdf-data",
            mime_type="application/pdf",
            ocr_mode="fallback",
        )
        assert result == mock_result

    @pytest.mark.asyncio
    async def test_ocr_mode_resolved_from_capability_default(self):
        cap = self._make_capability(ocr_mode="never")

        mock_result = {"text": "", "metadata": {}}
        with patch.object(_ocr_mod, "TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(return_value=mock_result)

            await cap.extract_text(
                file_bytes=b"data",
                mime_type="text/plain",
                mode=None,
            )

        instance.extract_text.assert_called_once_with(
            file_bytes=b"data",
            mime_type="text/plain",
            ocr_mode="never",
        )

    @pytest.mark.asyncio
    async def test_audit_log_emitted(self, caplog):
        cap = self._make_capability()

        mock_result = {"text": "ok", "metadata": {}}
        with patch.object(_ocr_mod, "TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(return_value=mock_result)

            with caplog.at_level(logging.INFO, logger="shu.plugins.host.ocr_capability"):
                await cap.extract_text(
                    file_bytes=b"data",
                    mime_type="text/plain",
                )

        assert any("host.ocr.extract_text" in record.message for record in caplog.records)


# ===========================================================================
# 4. AttachmentService._fast_extract_text
# ===========================================================================


class TestAttachmentServiceFastExtraction:
    """Test error handling in AttachmentService._fast_extract_text."""

    @staticmethod
    def _make_service():
        from shu.services.attachment_service import AttachmentService

        mock_session = MagicMock()
        with (
            patch("shu.services.attachment_service.get_settings_instance") as mock_settings_fn,
            patch("shu.services.attachment_service.Path.mkdir"),
        ):
            settings = MagicMock()
            settings.chat_attachment_storage_dir = "/tmp/test_attachments"
            settings.chat_attachment_allowed_types = ["pdf", "txt"]
            mock_settings_fn.return_value = settings
            return AttachmentService(db_session=mock_session)

    @pytest.mark.asyncio
    async def test_unsupported_format_returns_empty_no_retry(self):
        from shu.processors.text_extractor import UnsupportedFileFormatError

        service = self._make_service()

        with patch("shu.services.attachment_service.TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(side_effect=UnsupportedFileFormatError(".xyz"))

            text, meta = await service._fast_extract_text(Path("file.xyz"))

        assert text == ""
        assert "unsupported format" in meta["details"]["error"]
        instance.extract_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_transient_io_error_retries_succeeds(self):
        service = self._make_service()

        success_result = {"text": "extracted text", "metadata": {"method": "fast_extraction"}}
        with patch("shu.services.attachment_service.TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(
                side_effect=[OSError("disk error"), success_result],
            )

            text, meta = await service._fast_extract_text(Path("file.pdf"))

        assert text == "extracted text"
        assert instance.extract_text.call_count == 2

    @pytest.mark.asyncio
    async def test_import_error_propagates(self):
        service = self._make_service()

        with patch("shu.services.attachment_service.TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(side_effect=ImportError("missing dep"))

            with pytest.raises(ImportError, match="missing dep"):
                await service._fast_extract_text(Path("file.pdf"))

    @pytest.mark.asyncio
    async def test_type_error_propagates(self):
        service = self._make_service()

        with patch("shu.services.attachment_service.TextExtractor") as mock_cls:
            instance = mock_cls.return_value
            instance.extract_text = AsyncMock(side_effect=TypeError("bad arg"))

            with pytest.raises(TypeError, match="bad arg"):
                await service._fast_extract_text(Path("file.pdf"))
