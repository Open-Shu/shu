"""OCRService protocol, OCRResult dataclass, and DI wiring.

Provides an abstract interface for OCR text extraction with two backends:
- ExternalOCRService: Mistral OCR via OpenRouter (when SHU_MISTRAL_OCR_API_KEY is set)
- LocalOCRService: EasyOCR/Tesseract via TextExtractor (default fallback)

DI wiring:
    - get_ocr_service()  — singleton factory (workers, services, FastAPI Depends())
    - reset_ocr_service() — test teardown
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..processors.text_extractor import TextExtractor
from .config import get_settings_instance
from .logging import get_logger

if TYPE_CHECKING:
    from .config import ConfigurationManager

logger = get_logger(__name__)

# Maps OCR-eligible MIME types to their data-URL encoding prefix.
# Used both as the eligibility gate (formats where OCR can extract text
# that fast text extraction can't) and for encoding payloads to the
# external OCR API. Non-OCR formats (docx, txt, html) are fully
# handled by TextExtractor and should never be sent to an OCR provider.
OCR_ELIGIBLE_MIME_PREFIXES: dict[str, str] = {
    "application/pdf": "data:application/pdf;base64,",
    "image/png": "data:image/png;base64,",
    "image/jpeg": "data:image/jpeg;base64,",
    "image/jpg": "data:image/jpeg;base64,",
    "image/gif": "data:image/gif;base64,",
    "image/tiff": "data:image/tiff;base64,",
    "image/bmp": "data:image/bmp;base64,",
    "image/webp": "data:image/webp;base64,",
}


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

    async def extract_text(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text from a document using OCR.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document (e.g., "application/pdf", "image/png").
            user_id: Optional user attribution for llm_usage rows written by
                billable OCR providers (ExternalOCRService). Local OCR ignores it.

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


async def extract_text_with_ocr_fallback(
    file_bytes: bytes,
    mime_type: str,
    config_manager: ConfigurationManager,
    *,
    filename: str | None = None,
    ocr_mode: str = "auto",
    user_id: str | None = None,
) -> dict:
    """Two-step text extraction: fast extraction first, OCR service if needed.

    Step 1 uses TextExtractor with ocr_mode="text_only" (PDF text, DOCX, etc.).
    Step 2 routes through get_ocr_service() (Mistral or local EasyOCR/Tesseract)
    only when step 1 yields insufficient text and ocr_mode permits it.

    "auto" and "fallback" behave identically: try fast text extraction,
    fall back to OCR when the result is below the minimum threshold.

    Args:
        file_bytes: Raw document bytes.
        mime_type: MIME type of the document.
        config_manager: ConfigurationManager for TextExtractor.
        filename: Optional filename for extension detection in fast extraction.
        ocr_mode: One of "auto", "always", "never", "fallback", "text_only".

    Returns:
        Dict with "text" and "metadata" keys, same shape as TextExtractor.extract_text().

    """
    effective_mode = (ocr_mode or "auto").strip().lower()
    settings = get_settings_instance()

    if effective_mode in ("never", "text_only"):
        return await TextExtractor(config_manager=config_manager).extract_text(
            file_bytes=file_bytes,
            mime_type=mime_type,
            ocr_mode="text_only",
            **({"file_path": filename} if filename else {}),
        )

    if effective_mode == "always":
        if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
            logger.warning(
                "ocr_mode='always' requested for non-OCR-eligible type %s, using text extraction",
                mime_type,
            )
            return await TextExtractor(config_manager=config_manager).extract_text(
                file_bytes=file_bytes,
                mime_type=mime_type,
                ocr_mode="text_only",
                **({"file_path": filename} if filename else {}),
            )
        return await _run_ocr_service(file_bytes, mime_type, effective_mode, user_id=user_id)

    # auto / fallback: try cheap text extraction, fall back to OCR if
    # the result is missing or below the minimum length threshold.
    try:
        result = await TextExtractor(config_manager=config_manager).extract_text(
            file_bytes=file_bytes,
            mime_type=mime_type,
            ocr_mode="text_only",
            **({"file_path": filename} if filename else {}),
        )
        extracted_text = result.get("text", "")
    except Exception:
        if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
            raise
        extracted_text = ""
        result = {"text": "", "metadata": {}}

    if len(extracted_text.strip()) >= settings.ocr_fallback_min_text_length:
        return result

    if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
        return result

    return await _run_ocr_service(file_bytes, mime_type, effective_mode)


async def _run_ocr_service(file_bytes: bytes, mime_type: str, ocr_mode: str, *, user_id: str | None = None) -> dict:
    """Call the configured OCR service and return a result dict with timing."""
    import time

    start = time.monotonic()
    ocr_service = get_ocr_service()
    ocr_result = await ocr_service.extract_text(file_bytes, mime_type, user_id=user_id)
    duration = time.monotonic() - start

    return {
        "text": ocr_result.text,
        "metadata": {
            "method": "ocr",
            "engine": ocr_result.engine,
            "confidence": ocr_result.confidence,
            "duration": duration,
            "details": {
                "page_count": ocr_result.page_count,
                "ocr_mode": ocr_mode,
                "processing_time": duration,
            },
        },
    }
