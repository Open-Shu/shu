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
from ..core.ocr_service import OCR_ELIGIBLE_MIME_PREFIXES, OCRResult
from ..llm.service import LLMService
from ..models.llm_provider import ModelType
from .usage_recording import get_usage_recorder

logger = get_logger(__name__)

_PROVIDER_TYPE_KEY = "generic_completions"
# Must stay in lockstep with the name seeded by
# backend/scripts/hosting_deployment.py::_seed_mistral_provider. Mistral is a
# general-purpose provider (chat, embedding, OCR); the row is named for the
# vendor, not the OCR capability. The "Shu Curated:" prefix is the epic-wide
# convention (SHU-713) for seeder-created rows so they cannot collide with
# customer-added entries.
_PROVIDER_NAME = "Shu Curated: Mistral"


def _coerce_non_negative_int(value: Any, fallback: int) -> int:
    """Coerce an untrusted external value to a non-negative int, or use fallback.

    The Mistral API's pages_processed field is user-facing billing data — we
    can't assume it's always a well-formed int. Negative, missing, or
    non-numeric values fall back to our own observed page count.
    """
    if value is None:
        return fallback
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return fallback
    return coerced if coerced >= 0 else fallback


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

    async def extract_text(
        self,
        file_bytes: bytes,
        mime_type: str,
        *,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text by sending the document to Mistral's OCR API.

        Args:
            file_bytes: Raw bytes of the document to process.
            mime_type: MIME type of the document.
            user_id: Optional user attribution for the resulting llm_usage row.

        Returns:
            OCRResult with extracted text from all pages.

        Raises:
            httpx.HTTPStatusError: On API errors (4xx/5xx).
            ValueError: On unsupported mime types.

        """
        prefix = OCR_ELIGIBLE_MIME_PREFIXES.get(mime_type)
        if prefix is None:
            raise ValueError(
                f"ExternalOCRService does not support mime type {mime_type!r}. "
                f"Supported: {sorted(OCR_ELIGIBLE_MIME_PREFIXES.keys())}"
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

        # pages_processed comes from the API — normalize it before cost math.
        usage_info = result.get("usage_info") or {}
        pages_processed = _coerce_non_negative_int(
            usage_info.get("pages_processed"),
            fallback=len(pages),
        )
        await self._record_usage(
            pages_processed, usage_info=usage_info, observed_page_count=len(pages), user_id=user_id
        )

        return OCRResult(
            text="\n\n".join(page_texts),
            engine=self._model_name,
            page_count=len(pages),
            confidence=confidence,
        )

    async def _resolve_provider_and_model(self, session) -> bool:
        """Look up pre-seeded provider and model records for usage tracking.

        Returns True if both were found and cached, False otherwise.
        Provider/model rows must be created by the hosting seed script;
        this method only performs lookups to avoid races under concurrency.
        """
        if self._provider_id is not None and self._model_id is not None:
            return True

        llm_service = LLMService(session)

        provider = await llm_service.get_provider_by_name(_PROVIDER_NAME)
        if provider is None:
            logger.error(
                "Mistral OCR provider %r not found — seed it via the hosting script",
                _PROVIDER_NAME,
            )
            return False

        self._provider_id = provider.id

        for m in provider.models:
            if m.model_name == self._model_name and m.model_type == ModelType.OCR:
                self._model_id = m.id
                return True

        logger.error(
            "Mistral OCR model %r not found on provider %s — seed it via the hosting script",
            self._model_name,
            provider.id,
        )
        return False

    async def _record_usage(
        self,
        page_count: int,
        *,
        usage_info: dict[str, Any] | None = None,
        observed_page_count: int | None = None,
        user_id: str | None = None,
    ) -> None:
        """Record OCR usage in llm_usage. Best-effort — failures are logged, not raised.

        Cost is computed by ``record_llm_usage`` from the resolved model's
        ``cost_per_input_unit`` (per-page rate for OCR model_type; see
        ``core/model_pricing.py``). Passing ``total_cost=Decimal(0)`` triggers
        the shared DB-rate fallback so OCR uses the same two-tier contract as
        chat / embedding — repricing is a one-entry change in
        ``model_pricing.py`` plus a restart.

        Always logs the raw usage payload so costs can be reconstructed from
        logs if the DB write fails.
        """
        # Log raw usage payload — unconditional, independent of DB state, so
        # cost reconstruction from logs works even if the DB write later fails.
        logger.info(
            "Mistral OCR usage (raw)",
            extra={
                "model": self._model_name,
                "raw_usage_info": usage_info or {},
                "observed_page_count": observed_page_count,
                "pages_billed": page_count,
            },
        )

        try:
            from ..core.database import get_async_session_local

            session_factory = get_async_session_local()
            async with session_factory() as session:
                if not await self._resolve_provider_and_model(session):
                    # Provider/model lookup failed (see upstream error in _resolve_provider_and_model).
                    # Billing loses a row for a real OCR call here — surface that explicitly so
                    # ops can correlate the raw_usage_info log above with missing llm_usage
                    # entries and drive the seed fix. See SHU-713.
                    logger.error(
                        "Dropping OCR llm_usage row — Mistral OCR provider/model not seeded. "
                        "Reconstruct cost from the raw_usage_info log above (pages_billed=%d). "
                        "Fix by seeding via the hosting script.",
                        page_count,
                        extra={
                            "model": self._model_name,
                            "pages_billed": page_count,
                            "usage_recording": "dropped",
                        },
                    )
                    return

                # page_count → input_tokens because llm_models.cost_per_input_unit
                # carries the per-page rate for OCR model_type (unit-agnostic column
                # rename landed in SHU-700). total_cost=Decimal(0) engages the
                # DB-rate fallback inside record_llm_usage. A future Mistral
                # release that returns cost on the wire will naturally hit the
                # provider-authoritative branch instead, no code change needed.
                await get_usage_recorder().record(
                    provider_id=self._provider_id,
                    model_id=self._model_id,
                    request_type="ocr",
                    user_id=user_id,
                    input_tokens=page_count,
                    output_tokens=0,
                    total_cost=Decimal(0),
                    request_metadata={"page_count": page_count},
                    session=session,
                )
                await session.commit()
        except Exception as e:
            logger.warning("Failed to record OCR usage: %s", e)
