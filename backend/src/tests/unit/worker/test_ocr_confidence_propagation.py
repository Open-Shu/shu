"""Unit tests for OCR confidence propagation through the TextExtractor call chain.

Verifies that real confidence values flow from EasyOCR/Tesseract all the way to
the dict returned by extract_text(), and that non-OCR paths store None.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extractor():
    """Return a TextExtractor with a minimal ConfigurationManager stub."""
    from shu.processors.text_extractor import TextExtractor

    settings = MagicMock()
    settings.ocr_max_concurrent_jobs = 1
    settings.ocr_render_scale = 2.0
    settings.ocr_page_timeout = 60

    config_manager = MagicMock()
    config_manager.settings = settings

    extractor = TextExtractor.__new__(TextExtractor)
    extractor.config_manager = config_manager
    extractor._current_sync_job_id = None
    extractor.supported_formats = {
        ".txt": extractor._extract_text_plain,
        ".pdf": None,  # PDF handled separately
    }
    extractor.supported_extensions = {".txt", ".pdf"}
    extractor._last_ocr_engine = None
    return extractor


# ---------------------------------------------------------------------------
# _extract_pdf_ocr_direct_inner — confidence propagation
# ---------------------------------------------------------------------------

class TestExtractPdfOcrDirectInner:
    """_extract_pdf_ocr_direct_inner must return (text, confidence) not just text."""

    @pytest.mark.asyncio
    async def test_returns_tuple_with_easyocr_confidence(self):
        """EasyOCR avg_confidence propagates out of _extract_pdf_ocr_direct_inner."""
        extractor = _make_extractor()

        expected_text = "hello world"
        expected_confidence = 0.93

        with patch.object(
            extractor,
            "_process_pdf_with_ocr_direct",
            new=AsyncMock(return_value=(expected_text, "ocr", expected_confidence)),
        ):
            # Minimal single-page PDF bytes
            doc = MagicMock()
            doc.__len__ = MagicMock(return_value=1)

            with patch("fitz.open", return_value=doc):
                result = await extractor._extract_pdf_ocr_direct_inner("test.pdf", b"%PDF-fake")

        assert isinstance(result, tuple), "Must return a tuple"
        assert len(result) == 2
        text, confidence = result
        assert text == expected_text
        assert confidence == expected_confidence

    @pytest.mark.asyncio
    async def test_returns_zero_confidence_on_ocr_failure(self):
        """Returns ('', 0.0) when OCR processing raises."""
        extractor = _make_extractor()

        with patch.object(
            extractor,
            "_process_pdf_with_ocr_direct",
            new=AsyncMock(side_effect=RuntimeError("OCR exploded")),
        ):
            doc = MagicMock()
            doc.__len__ = MagicMock(return_value=1)

            with patch("fitz.open", return_value=doc):
                text, confidence = await extractor._extract_pdf_ocr_direct_inner("test.pdf", b"%PDF-fake")

        assert text == ""
        assert confidence == 0.0


# ---------------------------------------------------------------------------
# _extract_text_pdf_with_progress — confidence in return tuple
# ---------------------------------------------------------------------------

class TestExtractTextPdfWithProgress:
    """_extract_text_pdf_with_progress must return (text, bool, float|None)."""

    @pytest.mark.asyncio
    async def test_ocr_path_returns_real_confidence(self):
        """When OCR runs, confidence from _extract_pdf_ocr_direct is threaded through."""
        extractor = _make_extractor()
        expected_confidence = 0.87

        with patch.object(
            extractor,
            "_extract_pdf_ocr_direct",
            new=AsyncMock(return_value=("ocr text", expected_confidence)),
        ):
            text, ocr_used, confidence = await extractor._extract_text_pdf_with_progress(
                "test.pdf", b"fake", use_ocr=True, ocr_mode="auto"
            )

        assert ocr_used is True
        assert confidence == expected_confidence

    @pytest.mark.asyncio
    async def test_text_only_path_returns_none_confidence(self):
        """When OCR is disabled, confidence is None."""
        extractor = _make_extractor()

        with patch.object(
            extractor,
            "_extract_pdf_text_only",
            new=AsyncMock(return_value="plain text"),
        ):
            text, ocr_used, confidence = await extractor._extract_text_pdf_with_progress(
                "test.pdf", b"fake", use_ocr=False, ocr_mode="auto"
            )

        assert ocr_used is False
        assert confidence is None

    @pytest.mark.asyncio
    async def test_fallback_mode_fast_success_returns_none_confidence(self):
        """Fallback mode: fast extraction success → confidence is None."""
        extractor = _make_extractor()

        with patch.object(
            extractor,
            "_extract_text_pdf_fast_only",
            new=AsyncMock(return_value="x" * 100),  # > 50 chars threshold
        ):
            text, ocr_used, confidence = await extractor._extract_text_pdf_with_progress(
                "test.pdf", b"fake", use_ocr=True, ocr_mode="fallback"
            )

        assert ocr_used is False
        assert confidence is None


# ---------------------------------------------------------------------------
# extract_text() — end-to-end confidence propagation
# ---------------------------------------------------------------------------

class TestExtractTextEndToEnd:
    """extract_text() must use the propagated confidence, not a hardcoded value."""

    @pytest.mark.asyncio
    async def test_easyocr_confidence_reaches_metadata(self):
        """Real EasyOCR confidence propagates all the way to extract_text() metadata."""
        extractor = _make_extractor()
        real_confidence = 0.91

        with patch.object(
            extractor,
            "_extract_text_direct",
            new=AsyncMock(return_value=("extracted text", True, real_confidence)),
        ):
            result = await extractor.extract_text("test.pdf", b"fake", use_ocr=True)

        assert result["metadata"]["confidence"] == real_confidence
        assert result["metadata"]["confidence"] != 0.8, "Must not be the old hardcoded value"

    @pytest.mark.asyncio
    async def test_tesseract_confidence_is_not_hardcoded(self):
        """Tesseract path: confidence is _calculate_text_quality output, not 0.8."""
        extractor = _make_extractor()
        quality_score = 0.65  # Simulated _calculate_text_quality result

        with patch.object(
            extractor,
            "_extract_text_direct",
            new=AsyncMock(return_value=("tesseract text", True, quality_score)),
        ):
            result = await extractor.extract_text("test.pdf", b"fake", use_ocr=True)

        assert result["metadata"]["confidence"] == quality_score
        assert result["metadata"]["confidence"] != 0.8

    @pytest.mark.asyncio
    async def test_fast_text_path_stores_none_confidence(self):
        """Non-OCR PDF extraction stores None for confidence."""
        extractor = _make_extractor()

        with patch.object(
            extractor,
            "_extract_text_direct",
            new=AsyncMock(return_value=("plain text", False, None)),
        ):
            result = await extractor.extract_text("test.pdf", b"fake", use_ocr=False)

        assert result["metadata"]["confidence"] is None

    @pytest.mark.asyncio
    async def test_txt_file_stores_none_confidence(self):
        """Plain text files (non-OCR) store None for confidence."""
        extractor = _make_extractor()

        with patch.object(
            extractor,
            "_extract_text_direct",
            new=AsyncMock(return_value=("plain text", False, None)),
        ):
            result = await extractor.extract_text("readme.txt", b"plain text", use_ocr=False)

        assert result["metadata"]["confidence"] is None


# ---------------------------------------------------------------------------
# _process_pdf_with_tesseract_direct — uses _calculate_text_quality
# ---------------------------------------------------------------------------

class TestTesseractConfidenceIsQualityHeuristic:
    """_process_pdf_with_tesseract_direct must use _calculate_text_quality, not 0.8."""

    def test_tesseract_confidence_matches_calculate_text_quality(self):
        """The confidence returned equals _calculate_text_quality(text)."""
        extractor = _make_extractor()

        page = MagicMock()
        pix = MagicMock()
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=1)
        doc.__getitem__ = MagicMock(return_value=page)

        fake_text = "the quick brown fox jumps over the lazy dog"

        with patch("pytesseract.image_to_string", return_value=fake_text), patch(
            "PIL.Image.open", return_value=MagicMock()
        ):
            text, method, confidence = extractor._process_pdf_with_tesseract_direct(doc, "test.pdf")

        expected_quality = extractor._calculate_text_quality(fake_text)
        assert confidence == expected_quality
        assert confidence != 0.8, "Must not be the old hardcoded value"
        assert method == "tesseract_direct"
