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
                b"pdf-bytes", "application/pdf", config_manager, ocr_mode=mode,
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
                b"pdf-bytes", "application/pdf", config_manager, ocr_mode="never",
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode="always",
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
                b"pdf-bytes", "application/pdf", config_manager, ocr_mode=mode,
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode=mode,
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode="auto",
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
                b"image-bytes", "image/png", MagicMock(), ocr_mode="auto",
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode="auto",
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode="auto",
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
                b"hi", "text/plain", MagicMock(), ocr_mode=mode,
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
                b"short", docx_mime, MagicMock(), ocr_mode="auto",
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
                b"pdf-bytes", "application/pdf", MagicMock(), ocr_mode="auto",
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
                b"text", "text/plain", MagicMock(), ocr_mode="always",
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
        ):
            with pytest.raises(RuntimeError, match="docx parser broke"):
                await extract_text_with_ocr_fallback(
                    b"docx-bytes",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    MagicMock(),
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
            result = await _run_ocr_service(b"data", "application/pdf", "auto")

        assert result["metadata"]["method"] == "ocr"
        assert result["metadata"]["engine"] == "mistral-ocr-latest"
        assert result["metadata"]["confidence"] == 0.95
        assert "duration" in result["metadata"]
        assert result["metadata"]["duration"] > 0
        assert result["metadata"]["details"]["page_count"] == 1
        assert result["metadata"]["details"]["ocr_mode"] == "auto"
        assert result["metadata"]["details"]["processing_time"] > 0
