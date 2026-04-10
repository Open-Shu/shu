"""ExternalOCRService — Mistral OCR via Mistral's native OCR API.

Sends the entire document as a base64 data URL to Mistral's /ocr endpoint.
The API handles PDF page splitting internally — no need to render pages
to images on our side.

Failures raise — no silent fallback to local. The worker retry mechanism
handles transient errors.
"""

import base64
from decimal import Decimal
from typing import Any

import httpx

from ..core.logging import get_logger
from ..core.ocr_service import OCRResult
from ..llm.service import LLMService
from ..models.llm_provider import ModelType
from .usage_recording import record_llm_usage

logger = get_logger(__name__)

# $1 per 1000 pages
_COST_PER_PAGE = Decimal("0.001")

_MIME_TO_DATA_URL_PREFIX: dict[str, str] = {
    "application/pdf": "data:application/pdf;base64,",
    "image/png": "data:image/png;base64,",
    "image/jpeg": "data:image/jpeg;base64,",
    "image/jpg": "data:image/jpeg;base64,",
    "image/gif": "data:image/gif;base64,",
    "image/tiff": "data:image/tiff;base64,",
    "image/bmp": "data:image/bmp;base64,",
    "image/webp": "data:image/webp;base64,",
}

_PROVIDER_TYPE_KEY = "generic_completions"
_PROVIDER_NAME = "Mistral OCR (auto-provisioned)"


class ExternalOCRService:
    """Calls Mistral OCR via Mistral's native /ocr API endpoint."""

    def __init__(self, api_key: str, api_base_url: str, model_name: str) -> None:
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._model_name = model_name
        self._provider_id: str | None = None
        self._model_id: str | None = None

    def __repr__(self) -> str:
        """Redact API key to prevent leaking credentials in logs/tracebacks."""
        return f"ExternalOCRService(model={self._model_name!r})"

    async def extract_text(self, file_bytes: bytes, mime_type: str) -> OCRResult:
        """Extract text by sending the document to Mistral's OCR API.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document.

        Returns:
            OCRResult with extracted text from all pages.

        Raises:
            httpx.HTTPStatusError: On API errors (4xx/5xx).
            ValueError: On unsupported mime types.

        """
        prefix = _MIME_TO_DATA_URL_PREFIX.get(mime_type)
        if prefix is None:
            raise ValueError(
                f"ExternalOCRService does not support mime type {mime_type!r}. "
                f"Supported: {sorted(_MIME_TO_DATA_URL_PREFIX.keys())}"
            )

        b64 = base64.b64encode(file_bytes).decode("ascii")
        data_url = f"{prefix}{b64}"

        payload: dict[str, Any] = {
            "model": self._model_name,
            "document": {
                "type": "document_url",
                "document_url": data_url,
            },
            "confidence_scores_granularity": "page",
            "table_format": None,  # inline MD tables
            "include_image_base64": False,  # TODO: we may be able to support ingestion intelligence on images in the future?
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._api_base_url}/ocr",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
            )
            response.raise_for_status()

        result = response.json()
        pages = result.get("pages", [])
        page_texts = [page.get("markdown", "") for page in pages]

        # Confidences are returned per page. We take the AVG of each and AVG those out.
        page_confidences = []
        for page in pages:
            scores = page.get("confidence_scores") or {}
            avg = scores.get("average_page_confidence_score")
            if avg is not None:
                page_confidences.append(avg)
        confidence = sum(page_confidences) / len(page_confidences) if page_confidences else None

        # Get pages processed from the reported usage stats so we record the usage correctly.
        usage_info = result.get("usage_info") or {}
        pages_processed = usage_info.get("pages_processed", len(pages))
        await self._record_usage(pages_processed)

        return OCRResult(
            text="\n\n".join(page_texts),
            engine=self._model_name,
            page_count=len(pages),
            confidence=confidence,
        )

    async def _ensure_provider_and_model(self, session) -> None:
        """Find or create the Mistral OCR provider and model records for usage tracking."""
        if self._provider_id is not None:
            return

        llm_service = LLMService(session)

        provider = await llm_service.get_provider_by_name(_PROVIDER_NAME)
        if provider is None:
            provider = await llm_service.create_provider(
                name=_PROVIDER_NAME,
                provider_type=_PROVIDER_TYPE_KEY,
                api_endpoint=self._api_base_url,
            )

        self._provider_id = provider.id

        existing_model = None
        for m in provider.models:
            if m.model_name == self._model_name and m.model_type == ModelType.OCR:
                existing_model = m
                break

        if existing_model is None:
            existing_model = await llm_service.create_model(
                provider_id=provider.id,
                model_name=self._model_name,
                display_name="Mistral OCR",
                model_type=ModelType.OCR,
            )

        self._model_id = existing_model.id

    async def _record_usage(self, page_count: int) -> None:
        """Record OCR usage in llm_usage. Best-effort — failures are logged, not raised."""
        try:
            from ..core.database import get_async_session_local

            total_cost = _COST_PER_PAGE * page_count
            session_factory = get_async_session_local()
            async with session_factory() as session:
                await self._ensure_provider_and_model(session)
                await record_llm_usage(
                    provider_id=self._provider_id,
                    model_id=self._model_id,
                    request_type="ocr",
                    input_cost=total_cost,
                    total_cost=total_cost,
                    request_metadata={"page_count": page_count},
                    session=session,
                )
        except Exception as e:
            logger.warning("Failed to record OCR usage: %s", e)
