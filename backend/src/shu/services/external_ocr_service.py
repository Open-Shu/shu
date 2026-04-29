"""ExternalOCRService — Mistral OCR via Mistral's native OCR API.

Sends the entire document as a base64 data URL to Mistral's /ocr endpoint.
The API handles PDF page splitting internally — no need to render pages
to images on our side.

Memory profile (SHU-738): the request body is built as a streaming async
iterator that base64-encodes the source document chunk-by-chunk. When the
caller passes a ``file_path``, only one ~256 KiB chunk lives in Python
memory at a time during transmission — no full-document buffer, no full
base64 string, no full data URL. The bytes-based path (used by plugins
and in-memory test harnesses) base64-encodes the supplied buffer once
and frees it before the request hits the wire.

Failures raise — no silent fallback to local. The worker retry mechanism
handles transient errors.
"""

import base64
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ..core.database import get_async_session_local
from ..core.external_model_resolver import ensure_provider_and_model_active
from ..core.logging import get_logger
from ..core.ocr_service import OCR_ELIGIBLE_MIME_PREFIXES, OCRResult
from ..llm.service import LLMService
from ..models.llm_provider import ModelType
from .usage_recording import get_usage_recorder

logger = get_logger(__name__)

# Read 192 KiB at a time. Multiple of 3 bytes so each chunk encodes to a
# whole number of base64 quadruplets and we never have to carry a partial
# triple across iterations. 192 KiB encoded → 256 KiB of ASCII output, which
# is the chunk size httpx sees on the wire.
_STREAM_CHUNK_SIZE = 192 * 1024

_PROVIDER_TYPE_KEY = "generic_completions"
# Must stay in lockstep with the name seeded by
# backend/scripts/hosting_deployment.py::_seed_mistral_provider. Mistral is a
# general-purpose provider (chat, embedding, OCR); the row is named for the
# vendor, not the OCR capability. The "Shu Curated:" prefix is the epic-wide
# convention (SHU-713) for seeder-created rows so they cannot collide with
# customer-added entries.
_PROVIDER_NAME = "Shu Curated: Mistral"


def _build_streaming_request_body(
    *,
    model: str,
    mime_prefix: str,
    file_bytes: bytes | None,
    file_path: str | None,
) -> tuple[AsyncIterator[bytes], int]:
    """Construct the Mistral /ocr request body as a streaming async iterator.

    Mistral's endpoint expects a single JSON document with the file embedded
    as a base64 data URL inside ``document.document_url``. To avoid holding
    the full document, the full base64 string, and the full data URL in
    memory simultaneously (the pre-SHU-738 pattern), we build the JSON
    envelope as three concatenated parts and base64-encode the file body
    chunk-by-chunk between them:

        prefix_bytes = b'{"model":"…","document":{"type":"document_url","document_url":"data:application/pdf;base64,'
        body_bytes   = b64(file_chunk_1) + b64(file_chunk_2) + …
        suffix_bytes = b'"},"confidence_scores_granularity":"page",…}'

    httpx accepts the iterator directly via ``content=``. The request body
    is well-formed JSON because base64 only emits ASCII characters in
    ``[A-Za-z0-9+/=]`` — none of which need JSON escaping inside a string.

    Returns the iterator and the exact ``Content-Length`` so the request
    can be sent without chunked transfer encoding (Mistral expects a
    fixed-length body and the value is computable upfront from
    ``ceil(file_size / 3) * 4``).
    """
    # Building the suffix as a JSON-serialized object lets the underlying
    # JSON library handle escaping for any future non-ASCII model name. The
    # key insight is that we never serialize the document content via JSON;
    # we splice raw base64 ASCII into the wire bytes directly.
    payload_envelope: dict[str, Any] = {
        "model": model,
        "document": {
            "type": "document_url",
            "document_url_PLACEHOLDER": "",  # replaced by streaming splice below
        },
        "confidence_scores_granularity": "page",
        "table_format": None,
        "include_image_base64": False,
    }
    serialized = json.dumps(payload_envelope, separators=(",", ":"))
    # Split the serialized envelope around the placeholder so we can stream
    # the data URL directly into its position.
    sentinel_key = '"document_url_PLACEHOLDER":""'
    split_index = serialized.index(sentinel_key)
    real_key = '"document_url":"' + mime_prefix
    prefix_str = serialized[:split_index] + real_key
    suffix_str = '"' + serialized[split_index + len(sentinel_key) :]
    prefix_bytes = prefix_str.encode("ascii")
    suffix_bytes = suffix_str.encode("ascii")

    # Determine source size + acquire a payload reference.
    if file_path is not None:
        source_size = Path(file_path).stat().st_size
        path_for_iter = file_path
        bytes_for_iter: bytes | None = None
    else:
        assert file_bytes is not None
        source_size = len(file_bytes)
        path_for_iter = None
        bytes_for_iter = file_bytes

    # base64 inflates by ceil(N/3)*4. For N divisible by 3 this is exactly
    # 4N/3; otherwise the last block is padded with '=' and the formula
    # still holds. We compute it without materializing the encoded data.
    encoded_size = ((source_size + 2) // 3) * 4
    content_length = len(prefix_bytes) + encoded_size + len(suffix_bytes)

    async def _iter() -> AsyncIterator[bytes]:
        yield prefix_bytes
        if path_for_iter is not None:
            # Read in chunks aligned to 3 bytes so each block encodes to
            # complete base64 quadruplets — no partial-triple carry across
            # iterations and no padding except in the final block.
            with open(path_for_iter, "rb") as fh:
                while True:
                    chunk = fh.read(_STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield base64.b64encode(chunk)
        else:
            assert bytes_for_iter is not None
            # The bytes branch can't avoid the one-time full encode (the
            # caller already has the buffer in memory). We still emit it
            # via the iterator so the request body holds at most one extra
            # copy at a time, and the encoded buffer is freed before the
            # response is read.
            yield base64.b64encode(bytes_for_iter)
        yield suffix_bytes

    return _iter(), content_length


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
        *,
        file_bytes: bytes | None = None,
        file_path: str | None = None,
        mime_type: str,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text by sending the document to Mistral's OCR API.

        Exactly one of ``file_bytes`` or ``file_path`` must be provided.
        ``file_path`` is the memory-efficient hot path: the request body is
        streamed to httpx as base64-encoded chunks, so no full document
        buffer or full data URL string is held in Python memory at any
        point. ``file_bytes`` is preserved for plugin and in-memory callers
        and base64-encodes the supplied buffer once before the request.

        Args:
            file_bytes: Raw bytes of the document. Mutually exclusive with file_path.
            file_path: Path to the document on disk. Mutually exclusive with file_bytes.
            mime_type: MIME type of the document.
            user_id: Optional user attribution for the resulting llm_usage row.

        Returns:
            OCRResult with extracted text from all pages.

        Raises:
            httpx.HTTPStatusError: On API errors (4xx/5xx).
            ValueError: On unsupported mime types or invalid arg combos.

        """
        if (file_bytes is None) == (file_path is None):
            raise ValueError("Provide exactly one of file_bytes or file_path")

        prefix = OCR_ELIGIBLE_MIME_PREFIXES.get(mime_type)
        if prefix is None:
            raise ValueError(
                f"ExternalOCRService does not support mime type {mime_type!r}. "
                f"Supported: {sorted(OCR_ELIGIBLE_MIME_PREFIXES.keys())}"
            )

        await self._ensure_active()

        body_iter, content_length = _build_streaming_request_body(
            model=self._model_name,
            mime_prefix=prefix,
            file_bytes=file_bytes,
            file_path=file_path,
        )
        # Drop the bytes reference now that the iterator owns its own copy
        # (or the file handle, on the path branch). Keeps peak memory low
        # for the bytes branch, which is otherwise still doing one full copy.
        del file_bytes

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._api_base_url}/ocr",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Content-Length": str(content_length),
                },
                content=body_iter,
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

    async def _ensure_active(self) -> None:
        """Gate the OCR call on provider/model is_active.

        Resolves the provider and model IDs first (lazy singleton pattern); if
        the seed is missing, skip the active check so the usage-recording path
        can emit the existing "dropped row" diagnostic — a missing row is a
        seed bug, not a deactivation signal.
        """
        session_factory = get_async_session_local()
        async with session_factory() as session:
            if not await self._resolve_provider_and_model(session):
                return
            await ensure_provider_and_model_active(self._provider_id, self._model_id, call_type="OCR", session=session)

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

        Cost is computed by ``UsageRecorder.record`` (via ``get_usage_recorder()``)
        from the resolved model's ``cost_per_input_unit`` — the per-page rate
        for OCR model_type; see ``core/model_pricing.py``. Passing
        ``total_cost=Decimal(0)`` triggers the shared DB-rate fallback so OCR
        uses the same two-tier contract as chat / embedding. Repricing is a
        one-entry change in ``model_pricing.py`` plus a restart.

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
                # DB-rate fallback inside UsageRecorder. A future Mistral
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
