"""LocalOCRService — wraps existing EasyOCR/Tesseract via TextExtractor.

TODO: TextExtractor currently owns both text extraction AND OCR orchestration.
LocalOCRService should own the local OCR path directly (EasyOCR/Tesseract),
and TextExtractor should be reduced to non-OCR text extraction only. The
current shape forces a round-trip through TextExtractor's `ocr_mode=ALWAYS`
branch when the orchestrator has already decided OCR should run.
"""

from typing import Any

from ..core.config import ConfigurationManager, get_config_manager
from ..core.logging import get_logger
from ..core.ocr_modes import OcrMode
from ..core.ocr_service import OCRResult
from ..processors.text_extractor import TextExtractor

logger = get_logger(__name__)


class LocalOCRService:
    """Delegates OCR to the existing TextExtractor pipeline."""

    def __init__(self, config_manager: ConfigurationManager | None = None) -> None:
        self._config_manager = config_manager or get_config_manager()

    async def extract_text(
        self,
        *,
        file_bytes: bytes | None = None,
        file_path: str | None = None,
        mime_type: str,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text using local EasyOCR/Tesseract via TextExtractor.

        Exactly one of ``file_bytes`` or ``file_path`` must be provided.
        ``file_path`` is the memory-efficient hot path: TextExtractor opens
        the PDF mmap-backed via ``_open_pdf(file_path, ...)``, avoiding the
        ``BytesIO``-wrapper copy that the bytes branch forces (SHU-738).

        Args:
            file_bytes: Raw bytes of the document. Mutually exclusive with file_path.
            file_path: Path to the document on disk. Mutually exclusive with file_bytes.
            mime_type: MIME type of the document.
            user_id: Accepted for protocol parity with ExternalOCRService;
                local OCR doesn't record llm_usage, so the value is unused.

        Returns:
            OCRResult with extracted text and metadata.

        """
        if (file_bytes is None) == (file_path is None):
            raise ValueError("Provide exactly one of file_bytes or file_path")

        extractor = TextExtractor(config_manager=self._config_manager)
        # The orchestrator has already decided OCR should run; pass `ALWAYS`
        # so TextExtractor doesn't re-evaluate. Passing `AUTO` happened to work
        # by accident pre-SHU-728 (the inner extractor mapped `auto` → use_ocr=True),
        # but it was a confusing latent dependency.
        kwargs: dict[str, Any] = {
            "mime_type": mime_type,
            "ocr_mode": OcrMode.ALWAYS.value,
        }
        if file_path is not None:
            kwargs["file_path"] = file_path
        else:
            kwargs["file_bytes"] = file_bytes
        result = await extractor.extract_text(**kwargs)

        metadata = result.get("metadata", {})
        return OCRResult(
            text=result.get("text", ""),
            engine=metadata.get("engine", "unknown"),
            page_count=metadata.get("details", {}).get("page_count"),
            confidence=metadata.get("confidence"),
        )
