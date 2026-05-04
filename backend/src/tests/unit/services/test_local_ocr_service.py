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
            result = await svc.extract_text(file_bytes=b"pdf bytes", mime_type="application/pdf")

        mock_cls.assert_called_once_with(config_manager=config_manager)
        # SHU-728: LocalOCRService passes "always" (not "auto") so the inner
        # TextExtractor doesn't re-evaluate the routing decision the
        # orchestrator has already made.
        mock_extractor.extract_text.assert_called_once_with(
            file_bytes=b"pdf bytes",
            mime_type="application/pdf",
            ocr_mode="always",
        )

        assert isinstance(result, OCRResult)
        assert result.text == "extracted text"
        assert result.engine == "easyocr"
        assert result.confidence == 0.85
        assert result.page_count == 2

    @pytest.mark.asyncio
    async def test_delegates_with_file_path_uses_mmap(self):
        """SHU-738: file_path branch passes file_path to TextExtractor (not file_bytes).

        TextExtractor's `_open_pdf` opens fitz mmap-backed when file_path is
        provided; the BytesIO copy that the bytes branch forces is the noisy-
        neighbor risk this whole change targets.
        """
        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(
            return_value={"text": "ocr from path", "metadata": {"engine": "easyocr"}}
        )

        config_manager = MagicMock()
        svc = LocalOCRService(config_manager=config_manager)

        with patch(
            "shu.services.local_ocr_service.TextExtractor",
            return_value=mock_extractor,
        ):
            result = await svc.extract_text(file_path="/tmp/staged.pdf", mime_type="application/pdf")

        mock_extractor.extract_text.assert_called_once_with(
            file_path="/tmp/staged.pdf",
            mime_type="application/pdf",
            ocr_mode="always",
        )
        # No file_bytes in the call kwargs — that's the whole point.
        call_kwargs = mock_extractor.extract_text.call_args.kwargs
        assert "file_bytes" not in call_kwargs
        assert result.text == "ocr from path"

    @pytest.mark.asyncio
    async def test_rejects_both_or_neither_input(self):
        svc = LocalOCRService(config_manager=MagicMock())
        with pytest.raises(ValueError, match="exactly one"):
            await svc.extract_text(file_bytes=b"x", file_path="/tmp/y", mime_type="application/pdf")
        with pytest.raises(ValueError, match="exactly one"):
            await svc.extract_text(mime_type="application/pdf")

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
            result = await svc.extract_text(file_bytes=b"data", mime_type="image/png")

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
                await svc.extract_text(file_bytes=b"data", mime_type="application/pdf")

    @patch("shu.services.local_ocr_service.get_config_manager")
    def test_default_config_manager(self, mock_get_cm):
        """Should use get_config_manager() if none provided."""
        mock_cm = MagicMock()
        mock_get_cm.return_value = mock_cm

        svc = LocalOCRService()
        assert svc._config_manager is mock_cm
