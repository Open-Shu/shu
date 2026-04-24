"""Unit tests for extract_text_with_ocr_fallback and _run_ocr_service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.ocr_service import (
    OCRResult,
    _run_ocr_service,
    extract_text_with_ocr_fallback,
    reset_ocr_service,
)


def _mock_settings(min_text_length: int = 50):
    settings = MagicMock()
    settings.ocr_fallback_min_text_length = min_text_length
    settings.mistral_ocr_api_key = None
    return settings


def _mock_text_extractor(text: str = "", metadata: dict | None = None):
    """Return a patched TextExtractor class whose extract_text returns given text."""
    mock_instance = MagicMock()
    mock_instance.extract_text = AsyncMock(
        return_value={
            "text": text,
            "metadata": metadata or {"method": "pdf_text", "engine": "pymupdf", "duration": 0.1},
        }
    )
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


def _mock_ocr_service(text: str = "OCR result", engine: str = "mistral-ocr"):
    svc = MagicMock()
    svc.extract_text = AsyncMock(
        return_value=OCRResult(text=text, engine=engine, page_count=1, confidence=0.95)
    )
    return svc


class TestExtractTextNeverMode:
    """text_only and never modes should never call the OCR service."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["never", "text_only"])
    async def test_never_calls_ocr(self, mode):
        mock_cls, mock_instance = _mock_text_extractor("Some extracted text")
        mock_settings = _mock_settings()
        config_manager = MagicMock()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=config_manager, ocr_mode=mode,
            )

        assert result["text"] == "Some extracted text"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_mode_returns_empty_when_no_text(self):
        mock_cls, _ = _mock_text_extractor("")
        mock_settings = _mock_settings()
        config_manager = MagicMock()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=config_manager, ocr_mode="never",
            )

        assert result["text"] == ""
        mock_get_ocr.assert_not_called()


class TestExtractTextAlwaysMode:
    """always mode should skip text extraction and go directly to OCR."""

    @pytest.mark.asyncio
    async def test_always_skips_text_extraction(self):
        ocr_svc = _mock_ocr_service("OCR text")
        mock_settings = _mock_settings()

        with (
            patch("shu.core.ocr_service.TextExtractor") as mock_cls,
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="always",
            )

        mock_cls.assert_not_called()
        assert result["text"] == "OCR text"
        assert result["metadata"]["method"] == "ocr"


class TestExtractTextAutoFallbackMode:
    """auto and fallback modes try text extraction first, OCR if insufficient."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["auto", "fallback"])
    async def test_sufficient_text_skips_ocr(self, mode):
        long_text = "A" * 100
        mock_cls, _ = _mock_text_extractor(long_text)
        mock_settings = _mock_settings(min_text_length=50)
        config_manager = MagicMock()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=config_manager, ocr_mode=mode,
            )

        assert result["text"] == long_text
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["auto", "fallback"])
    async def test_insufficient_text_triggers_ocr(self, mode):
        short_text = "tiny"
        mock_cls, _ = _mock_text_extractor(short_text)
        mock_settings = _mock_settings(min_text_length=50)
        ocr_svc = _mock_ocr_service("Full OCR text")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode=mode,
            )

        assert result["text"] == "Full OCR text"
        assert result["metadata"]["method"] == "ocr"

    @pytest.mark.asyncio
    async def test_empty_text_triggers_ocr(self):
        mock_cls, _ = _mock_text_extractor("")
        mock_settings = _mock_settings()
        ocr_svc = _mock_ocr_service("OCR from empty")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="auto",
            )

        assert result["text"] == "OCR from empty"

    @pytest.mark.asyncio
    async def test_text_extraction_exception_falls_back_to_ocr(self):
        """UnsupportedFileFormatError or other exceptions should trigger OCR."""
        mock_instance = MagicMock()
        mock_instance.extract_text = AsyncMock(
            side_effect=RuntimeError("Unsupported format")
        )
        mock_cls = MagicMock(return_value=mock_instance)
        mock_settings = _mock_settings()
        ocr_svc = _mock_ocr_service("OCR after error")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"image-bytes",
                mime_type="image/png",
                config_manager=MagicMock(), ocr_mode="auto",
            )

        assert result["text"] == "OCR after error"
        assert result["metadata"]["method"] == "ocr"

    @pytest.mark.asyncio
    async def test_configurable_threshold(self):
        """Threshold from settings should control the cutoff."""
        mock_cls, _ = _mock_text_extractor("A" * 30)
        ocr_svc = _mock_ocr_service("OCR text")

        # With threshold=20, 30 chars is sufficient
        mock_settings = _mock_settings(min_text_length=20)
        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="auto",
            )
        assert result["text"] == "A" * 30
        mock_get_ocr.assert_not_called()

        # With threshold=50, 30 chars is insufficient
        mock_settings = _mock_settings(min_text_length=50)
        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="auto",
            )
        assert result["text"] == "OCR text"


class TestOCRMimeTypeGate:
    """Non-OCR-eligible types (txt, docx, etc.) must never reach the OCR service."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["auto", "fallback"])
    async def test_short_text_file_skips_ocr(self, mode):
        """A short .txt should return the fast result, not fall through to OCR."""
        mock_cls, _ = _mock_text_extractor("hi")
        mock_settings = _mock_settings(min_text_length=50)

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"hi",
                mime_type="text/plain",
                config_manager=MagicMock(), ocr_mode=mode,
            )

        assert result["text"] == "hi"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_docx_skips_ocr(self):
        """A short .docx should return the fast result, not fall through to OCR."""
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        mock_cls, _ = _mock_text_extractor("short")
        mock_settings = _mock_settings(min_text_length=50)

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"short",
                mime_type=docx_mime,
                config_manager=MagicMock(), ocr_mode="auto",
            )

        assert result["text"] == "short"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_pdf_still_falls_through_to_ocr(self):
        """A short PDF IS OCR-eligible and should still fall through."""
        mock_cls, _ = _mock_text_extractor("x")
        mock_settings = _mock_settings(min_text_length=50)
        ocr_svc = _mock_ocr_service("OCR text from scanned PDF")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="auto",
            )

        assert result["text"] == "OCR text from scanned PDF"
        assert result["metadata"]["method"] == "ocr"

    @pytest.mark.asyncio
    async def test_always_mode_non_ocr_type_falls_back_to_text_extraction(self):
        """ocr_mode='always' with a non-OCR type should use text extraction, not crash."""
        mock_cls, _ = _mock_text_extractor("text content")
        mock_settings = _mock_settings()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"text",
                mime_type="text/plain",
                config_manager=MagicMock(), ocr_mode="always",
            )

        assert result["text"] == "text content"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ocr_type_extraction_exception_propagates(self):
        """Parser failure for a non-OCR type must raise, not return empty text."""
        mock_instance = MagicMock()
        mock_instance.extract_text = AsyncMock(side_effect=RuntimeError("docx parser broke"))
        mock_cls = MagicMock(return_value=mock_instance)
        mock_settings = _mock_settings()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_settings_instance", return_value=mock_settings),
            pytest.raises(RuntimeError, match="docx parser broke"),
        ):
            await extract_text_with_ocr_fallback(
                file_bytes=b"docx-bytes",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )


class TestRunOCRService:
    """Test _run_ocr_service timing and metadata shape."""

    def setup_method(self):
        reset_ocr_service()

    def teardown_method(self):
        reset_ocr_service()

    @pytest.mark.asyncio
    async def test_returns_duration_in_metadata(self):
        ocr_svc = _mock_ocr_service("text", engine="mistral-ocr-latest")

        with patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc):
            result = await _run_ocr_service("application/pdf", "auto", file_bytes=b"data")

        assert result["metadata"]["method"] == "ocr"
        assert result["metadata"]["engine"] == "mistral-ocr-latest"
        assert result["metadata"]["confidence"] == 0.95
        assert "duration" in result["metadata"]
        assert result["metadata"]["duration"] > 0
        assert result["metadata"]["details"]["page_count"] == 1
        assert result["metadata"]["details"]["ocr_mode"] == "auto"
        assert result["metadata"]["details"]["processing_time"] > 0


class TestUserIdThreading:
    """SHU-700 regression tests: user_id must reach the OCR service regardless
    of which branch of extract_text_with_ocr_fallback fires.

    An earlier user_id plumbing edit caught the ``ocr_mode='always'`` path but
    missed the ``auto`` / ``fallback`` path — the latter dropped user_id on its
    way to ``_run_ocr_service``. That bug left every auto-mode OCR row in
    ``llm_usage`` with NULL ``user_id`` despite the ingestion worker passing
    one correctly. These tests guard both branches.
    """

    @pytest.mark.asyncio
    async def test_user_id_reaches_ocr_service_on_auto_fallback_path(self):
        """Real-world path: text extraction below threshold → falls back to OCR."""
        mock_extractor_cls, _ = _mock_text_extractor(text="short")  # below default min
        mock_ocr_svc = _mock_ocr_service()

        with patch("shu.core.ocr_service.TextExtractor", mock_extractor_cls), patch(
            "shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc
        ), patch("shu.core.ocr_service.get_settings_instance", return_value=_mock_settings(50)):
            reset_ocr_service()
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
                user_id="user-abc",
            )

        mock_ocr_svc.extract_text.assert_called_once()
        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") == "user-abc", (
            "user_id must be forwarded to the OCR service on the auto/fallback path"
        )

    @pytest.mark.asyncio
    async def test_user_id_reaches_ocr_service_on_always_path(self):
        """ocr_mode='always' forces OCR; user_id must still thread through."""
        mock_ocr_svc = _mock_ocr_service()

        with patch("shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc), patch(
            "shu.core.ocr_service.get_settings_instance", return_value=_mock_settings()
        ):
            reset_ocr_service()
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="always",
                user_id="user-xyz",
            )

        mock_ocr_svc.extract_text.assert_called_once()
        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") == "user-xyz"

    @pytest.mark.asyncio
    async def test_user_id_default_is_none_when_not_provided(self):
        """Regression guard: callers that don't pass user_id still get None-safe behavior."""
        mock_extractor_cls, _ = _mock_text_extractor(text="short")
        mock_ocr_svc = _mock_ocr_service()

        with patch("shu.core.ocr_service.TextExtractor", mock_extractor_cls), patch(
            "shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc
        ), patch("shu.core.ocr_service.get_settings_instance", return_value=_mock_settings(50)):
            reset_ocr_service()
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(), ocr_mode="auto"
            )

        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") is None
