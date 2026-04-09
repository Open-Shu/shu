"""OCRService protocol, OCRResult dataclass, and DI wiring.

Provides an abstract interface for OCR text extraction with two backends:
- ExternalOCRService: Mistral OCR via OpenRouter (when SHU_MISTRAL_OCR_API_KEY is set)
- LocalOCRService: EasyOCR/Tesseract via TextExtractor (default fallback)

DI wiring:
    - get_ocr_service()  — singleton factory (workers, services, FastAPI Depends())
    - reset_ocr_service() — test teardown
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .config import get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class OCRResult:
    """Result of an OCR text extraction operation."""

    text: str
    engine: str
    page_count: int | None = None
    confidence: float | None = None


@runtime_checkable
class OCRService(Protocol):
    """Protocol for OCR text extraction services."""

    async def extract_text(self, file_bytes: bytes, mime_type: str) -> OCRResult:
        """Extract text from a document using OCR.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document (e.g., "application/pdf", "image/png").

        Returns:
            OCRResult with extracted text and metadata.

        """
        ...


# Module-level singleton
_ocr_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    """Get the configured OCR service (singleton).

    Resolution is purely settings-based (no DB query), so this is sync.
    SHU_MISTRAL_OCR_API_KEY set → ExternalOCRService, otherwise → LocalOCRService.

    Usable everywhere: workers, services, FastAPI Depends().
    """
    global _ocr_service  # noqa: PLW0603

    if _ocr_service is not None:
        return _ocr_service

    settings = get_settings_instance()

    if settings.mistral_ocr_api_key:
        from ..services.external_ocr_service import ExternalOCRService

        logger.info(
            "Using external OCR service (Mistral OCR)",
            extra={
                "model": settings.mistral_ocr_model,
                "base_url": settings.mistral_ocr_base_url,
            },
        )
        _ocr_service = ExternalOCRService(
            api_key=settings.mistral_ocr_api_key,
            api_base_url=settings.mistral_ocr_base_url,
            model_name=settings.mistral_ocr_model,
        )
        return _ocr_service

    from ..services.local_ocr_service import LocalOCRService

    logger.info("Using local OCR service (EasyOCR/Tesseract)")
    _ocr_service = LocalOCRService()
    return _ocr_service


def reset_ocr_service() -> None:
    """Reset the OCR service singleton (for testing only)."""
    global _ocr_service  # noqa: PLW0603
    _ocr_service = None
