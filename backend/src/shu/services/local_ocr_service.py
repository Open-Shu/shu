"""LocalOCRService — wraps existing EasyOCR/Tesseract via TextExtractor."""

from shu.core.config import ConfigurationManager, get_config_manager
from shu.core.logging import get_logger
from shu.core.ocr_service import OCRResult

logger = get_logger(__name__)


class LocalOCRService:
    """Delegates OCR to the existing TextExtractor pipeline."""

    def __init__(self, config_manager: ConfigurationManager | None = None) -> None:
        self._config_manager = config_manager or get_config_manager()

    async def extract_text(self, file_bytes: bytes, mime_type: str) -> OCRResult:
        """Extract text using local EasyOCR/Tesseract via TextExtractor.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document.

        Returns:
            OCRResult with extracted text and metadata.

        """
        from shu.processors.text_extractor import TextExtractor

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
