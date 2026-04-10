"""Unit tests for LocalOCRService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.ocr_service import OCRResult
from shu.services.local_ocr_service import LocalOCRService


class TestLocalOCRService:
    """Test LocalOCRService delegates to TextExtractor correctly."""

    @pytest.mark.asyncio
    async def test_delegates_to_text_extractor(self):
        """extract_text should call TextExtractor.extract_text with correct params."""
        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(
            return_value={
                "text": "extracted text",
                "metadata": {
                    "method": "ocr",
                    "engine": "easyocr",
                    "confidence": 0.85,
                    "details": {"page_count": 2},
                },
            }
        )

        config_manager = MagicMock()
        svc = LocalOCRService(config_manager=config_manager)

        with patch(
            "shu.services.local_ocr_service.TextExtractor",
            return_value=mock_extractor,
        ) as mock_cls:
            result = await svc.extract_text(b"pdf bytes", "application/pdf")

        mock_cls.assert_called_once_with(config_manager=config_manager)
        mock_extractor.extract_text.assert_called_once_with(
            file_bytes=b"pdf bytes",
            mime_type="application/pdf",
            ocr_mode="auto",
        )

        assert isinstance(result, OCRResult)
        assert result.text == "extracted text"
        assert result.engine == "easyocr"
        assert result.confidence == 0.85
        assert result.page_count == 2

    @pytest.mark.asyncio
    async def test_maps_result_with_missing_metadata(self):
        """Should handle missing metadata fields gracefully."""
        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(
            return_value={"text": "some text", "metadata": {}}
        )

        config_manager = MagicMock()
        svc = LocalOCRService(config_manager=config_manager)

        with patch(
            "shu.services.local_ocr_service.TextExtractor",
            return_value=mock_extractor,
        ):
            result = await svc.extract_text(b"data", "image/png")

        assert result.text == "some text"
        assert result.engine == "unknown"
        assert result.confidence is None
        assert result.page_count is None

    @pytest.mark.asyncio
    async def test_propagates_exceptions(self):
        """Errors from TextExtractor should propagate, not be swallowed."""
        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(side_effect=RuntimeError("OCR failed"))

        config_manager = MagicMock()
        svc = LocalOCRService(config_manager=config_manager)

        with patch(
            "shu.services.local_ocr_service.TextExtractor",
            return_value=mock_extractor,
        ):
            with pytest.raises(RuntimeError, match="OCR failed"):
                await svc.extract_text(b"data", "application/pdf")

    @patch("shu.services.local_ocr_service.get_config_manager")
    def test_default_config_manager(self, mock_get_cm):
        """Should use get_config_manager() if none provided."""
        mock_cm = MagicMock()
        mock_get_cm.return_value = mock_cm

        svc = LocalOCRService()
        assert svc._config_manager is mock_cm
