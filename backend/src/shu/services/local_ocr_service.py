"""LocalOCRService — wraps existing EasyOCR/Tesseract via TextExtractor.

TODO: TextExtractor currently owns both text extraction AND OCR orchestration.
LocalOCRService should own the local OCR path directly (EasyOCR/Tesseract),
and TextExtractor should be reduced to non-OCR text extraction only.
This avoids the round-trip where LocalOCRService calls TextExtractor with
ocr_mode="auto", which re-enters the same OCR logic that should live here.
"""

from ..core.config import ConfigurationManager, get_config_manager
from ..core.logging import get_logger
from ..core.ocr_service import OCRResult
from ..processors.text_extractor import TextExtractor

logger = get_logger(__name__)


class LocalOCRService:
    """Delegates OCR to the existing TextExtractor pipeline."""

    def __init__(self, config_manager: ConfigurationManager | None = None) -> None:
        self._config_manager = config_manager or get_config_manager()

    async def extract_text(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text using local EasyOCR/Tesseract via TextExtractor.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document.
            user_id: Accepted for protocol parity with ExternalOCRService;
                local OCR doesn't record llm_usage, so the value is unused.

        Returns:
            OCRResult with extracted text and metadata.

        """
        extractor = TextExtractor(config_manager=self._config_manager)
        result = await extractor.extract_text(
            file_bytes=file_bytes,
            mime_type=mime_type,
            ocr_mode="auto",
        )

        metadata = result.get("metadata", {})
        return OCRResult(
            text=result.get("text", ""),
            engine=metadata.get("engine", "unknown"),
            page_count=metadata.get("details", {}).get("page_count"),
            confidence=metadata.get("confidence"),
        )
