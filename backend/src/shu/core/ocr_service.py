"""OCRService protocol, OCRResult dataclass, and DI wiring.

Provides an abstract interface for OCR text extraction with two backends:
- ExternalOCRService: Mistral OCR via OpenRouter (when SHU_MISTRAL_OCR_API_KEY is set)
- LocalOCRService: EasyOCR/Tesseract via TextExtractor (default fallback)

DI wiring:
    - get_ocr_service()  — singleton factory (workers, services, FastAPI Depends())
    - reset_ocr_service() — test teardown
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import fitz

from ..processors.text_extractor import TextExtractor
from .config import get_settings_instance
from .logging import get_logger
from .ocr_modes import OcrMode
from .ocr_routing import RoutingDecision, RoutingThresholds, classify_pdf

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
        *,
        file_bytes: bytes | None = None,
        file_path: str | None = None,
        mime_type: str,
        user_id: str | None = None,
    ) -> OCRResult:
        """Extract text from a document using OCR.

        Exactly one of ``file_bytes`` or ``file_path`` must be provided. The
        ingestion worker passes ``file_path`` so providers can stream from
        disk without materializing the full document in Python memory
        (SHU-738). Plugin host callers and in-memory test harnesses still
        pass ``file_bytes``.

        Args:
            file_bytes: Raw bytes of the document. Mutually exclusive with file_path.
            file_path: Path to the document on local disk. Preferred for the
                ingestion-worker hot path — avoids the multi-megabyte buffer
                copies that the bytes path forces. Mutually exclusive with file_bytes.
            mime_type: MIME type of the document (e.g., "application/pdf", "image/png").
            user_id: Optional user attribution for llm_usage rows written by
                billable OCR providers (ExternalOCRService). Local OCR ignores it.

        Returns:
            OCRResult with extracted text and metadata.

        """
        ...


# Module-level singleton. Reused across calls because (a) LocalOCRService
# initializes EasyOCR/Tesseract models on construction and rebuilding per call
# is measurably slow on the ingestion hot path, and (b) ExternalOCRService
# holds a resolved provider/model ID cache that the SHU-705 active-check TTL
# cache depends on to avoid per-call DB lookups.
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


def select_initial_workload_type(mime_type: str, ocr_mode: str | OcrMode):
    """Decide which workload queue an upload should enter (SHU-739).

    Returns the right `WorkloadType` for the upload path to enqueue based on
    MIME type and the requested OCR mode. The split lets each stage run under
    its own per-process semaphore, which decouples the synchronized classifier
    spike and the per-doc text-extraction memory peak from the OCR throughput
    cap.

    Routing:
        | MIME                         | NEVER | ALWAYS | AUTO     |
        | ---------------------------- | ----- | ------ | -------- |
        | non-OCR-eligible (DOCX, txt) | TEXT  | TEXT   | TEXT     |
        | OCR-eligible non-PDF (image) | TEXT  | OCR    | OCR      |
        | PDF                          | TEXT  | OCR    | CLASSIFY |

    The CLASSIFY queue is only entered for PDFs in AUTO mode; the classifier
    decides between TEXT and OCR for that document and enqueues the next
    stage. Other rows skip CLASSIFY because there's nothing to decide.
    """
    from .ocr_modes import coerce_ocr_mode
    from .workload_routing import WorkloadType

    # Lenient coercion at the upload boundary — None/empty/unknown fall back
    # to AUTO, matching the existing plugin-host policy.
    effective_mode = coerce_ocr_mode(ocr_mode)

    if effective_mode is OcrMode.NEVER:
        return WorkloadType.INGESTION_TEXT

    if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
        return WorkloadType.INGESTION_TEXT

    if effective_mode is OcrMode.ALWAYS:
        return WorkloadType.INGESTION_OCR

    # AUTO: PDFs go through the classifier; OCR-eligible non-PDFs (images) go
    # straight to OCR because there's no per-page text geometry to classify.
    if mime_type == "application/pdf":
        return WorkloadType.INGESTION_CLASSIFY
    return WorkloadType.INGESTION_OCR


async def classify_pdf_routing(file_path: str | None, file_bytes: bytes | None) -> RoutingDecision:
    """Run `classify_pdf` and return the routing decision (SHU-739).

    Used by the `_handle_classify_job` worker to decide which downstream
    stage (INGESTION_TEXT or INGESTION_OCR) to enqueue. The `fitz.Document`
    is opened, scanned, and closed entirely inside one executor task — the
    handle never crosses thread boundaries, satisfying PyMuPDF's
    single-thread rule. The decision is the only thing returned to the
    event loop.

    NOTE: this is a stricter contract than the legacy
    `_classify_pdf_for_routing` (which opens on the event-loop thread,
    classifies in an executor, and returns the live doc to the caller).
    The legacy function is retained for `extract_text_with_ocr_fallback`'s
    in-process plugin path, which still hands the open doc off to
    `TextExtractor`. Reworking that hand-off is a separate ticket.
    """
    import asyncio

    thresholds = RoutingThresholds.from_settings()

    def _open_classify_close() -> RoutingDecision:
        doc = fitz.open(file_path) if file_path is not None else fitz.open(stream=file_bytes, filetype="pdf")
        try:
            return classify_pdf(doc, thresholds)
        finally:
            try:
                doc.close()
            except Exception:
                pass
            try:
                fitz.TOOLS.store_shrink(100)
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _open_classify_close)


async def extract_text_only(
    *,
    mime_type: str,
    config_manager: ConfigurationManager,
    file_bytes: bytes | None = None,
    file_path: str | None = None,
    filename: str | None = None,
) -> dict:
    """Non-OCR text extraction entry point used by `_handle_text_extract_job`.

    Used after `select_initial_workload_type` (or the classifier) routed the
    document to the text-only path. Calls `TextExtractor` with `ocr_mode=NEVER`
    so OCR is never attempted, regardless of the original upload mode.
    """
    extractor_kwargs: dict = {"mime_type": mime_type, "ocr_mode": OcrMode.NEVER.value}
    if filename:
        extractor_kwargs["filename"] = filename
    if file_path is not None:
        extractor_kwargs["file_path"] = file_path
    else:
        extractor_kwargs["file_bytes"] = file_bytes
    return await TextExtractor(config_manager=config_manager).extract_text(**extractor_kwargs)


async def extract_via_ocr(
    *,
    mime_type: str,
    file_bytes: bytes | None = None,
    file_path: str | None = None,
    user_id: str | None = None,
    ocr_mode: OcrMode = OcrMode.AUTO,
) -> dict:
    """OCR-only extraction entry point used by `_handle_ocr_job` (SHU-739).

    The post-split `_handle_ocr_job` calls this directly — no classifier
    preamble, no fallback to text extraction. The classifier (or the upload
    path's MIME-aware routing) has already decided OCR is the right choice
    by the time this runs.

    Falls back to text extraction only when `mime_type` is not OCR-eligible,
    matching the existing safety check in `extract_text_with_ocr_fallback`.
    """
    if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
        # Defensive: callers should not have routed a non-OCR-eligible MIME
        # here, but if they did, fall back to text extraction rather than
        # sending garbage to an OCR provider.
        logger.warning(
            "extract_via_ocr called with non-OCR-eligible mime %s, falling back to text extraction",
            mime_type,
        )
        from .config import get_config_manager

        return await extract_text_only(
            mime_type=mime_type,
            config_manager=get_config_manager(),
            file_bytes=file_bytes,
            file_path=file_path,
        )
    return await _run_ocr_service(
        mime_type, ocr_mode.value, file_bytes=file_bytes, file_path=file_path, user_id=user_id
    )


async def extract_text_with_ocr_fallback(  # noqa: PLR0912 — dispatcher: branches map to mode x mime-type x decision combos
    *,
    mime_type: str,
    config_manager: ConfigurationManager,
    file_bytes: bytes | None = None,
    file_path: str | None = None,
    filename: str | None = None,
    ocr_mode: str | OcrMode = OcrMode.AUTO,
    user_id: str | None = None,
) -> dict:
    """Two-step text extraction: classify, then either text-extract or OCR.

    Exactly one of ``file_bytes`` or ``file_path`` must be provided. The
    ingestion worker passes ``file_path`` so MuPDF can use mmap-backed reads
    via the OS page cache instead of holding the file in Python memory.
    Callers that genuinely operate on in-memory content (plugins, test
    harnesses) may pass ``file_bytes``.

    Behaviour by mode:

    - ``AUTO`` (default): for PDFs, run the per-page real-text classifier
      (`core.ocr_routing.classify_pdf`) and route to OCR or text extraction
      based on the result. For OCR-eligible image MIME types the classifier
      doesn't apply (no pages, no text-block geometry) and the file always
      goes to OCR. For non-OCR-eligible types the file always goes to text
      extraction.
    - ``ALWAYS``: skip the classifier and run OCR directly (falls back to
      text extraction for non-OCR-eligible types).
    - ``NEVER``: text extraction only, no OCR under any circumstances.

    Args:
        mime_type: MIME type of the document.
        config_manager: ConfigurationManager for TextExtractor.
        file_bytes: Raw document bytes (mutually exclusive with file_path).
        file_path: Path to the document on local disk (preferred; avoids
            loading the file into Python memory for the text-extraction path).
        filename: Optional filename for extension detection (used when
            file_bytes is supplied without file_path, or when file_path is
            a tempfile with no informative suffix).
        ocr_mode: One of the `OcrMode` values, or the equivalent string.
        user_id: Optional user attribution for billable OCR providers.

    Returns:
        Dict with "text" and "metadata" keys, same shape as TextExtractor.extract_text().

    """
    if (file_bytes is None) == (file_path is None):
        raise ValueError("Provide exactly one of file_bytes or file_path")

    # Strict validation at this trusted in-tree boundary. Plugin host
    # capabilities use `coerce_ocr_mode` for lenient string→enum coercion at
    # their untrusted entry points and pass valid OcrMode values down here.
    # Legacy strings ("fallback", "text_only") raise the same ValueError as
    # any other invalid mode — no backward-compat aliases per SHU-728.
    if isinstance(ocr_mode, OcrMode):
        effective_mode = ocr_mode
    else:
        try:
            effective_mode = OcrMode((ocr_mode or "").strip().lower())
        except ValueError as e:
            raise ValueError(f"Invalid ocr_mode {ocr_mode!r}; must be one of " f"{[m.value for m in OcrMode]}") from e

    extractor_kwargs: dict = {"mime_type": mime_type, "ocr_mode": OcrMode.NEVER.value}
    if filename:
        # Original upload filename is the most reliable source of the type extension;
        # staging paths always have a `.bin` suffix that carries no type information.
        extractor_kwargs["filename"] = filename
    if file_path is not None:
        extractor_kwargs["file_path"] = file_path
    else:
        extractor_kwargs["file_bytes"] = file_bytes

    if effective_mode is OcrMode.NEVER:
        return await TextExtractor(config_manager=config_manager).extract_text(**extractor_kwargs)

    if effective_mode is OcrMode.ALWAYS:
        if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
            logger.warning(
                "ocr_mode='always' requested for non-OCR-eligible type %s, using text extraction",
                mime_type,
            )
            return await TextExtractor(config_manager=config_manager).extract_text(**extractor_kwargs)
        return await _run_ocr_service(
            mime_type, effective_mode.value, file_bytes=file_bytes, file_path=file_path, user_id=user_id
        )

    # AUTO. Three sub-cases by MIME:
    # 1. Non-OCR-eligible (DOCX, txt, html, ...) — text extraction is the only path.
    # 2. OCR-eligible image — no per-page geometry to classify; OCR is the only path.
    # 3. PDF — run the classifier and route accordingly.
    if mime_type not in OCR_ELIGIBLE_MIME_PREFIXES:
        return await TextExtractor(config_manager=config_manager).extract_text(**extractor_kwargs)

    if mime_type != "application/pdf":
        return await _run_ocr_service(
            mime_type, effective_mode.value, file_bytes=file_bytes, file_path=file_path, user_id=user_id
        )

    thresholds = RoutingThresholds.from_settings()
    log_filename = filename or file_path or "<bytes>"
    decision, doc = await _classify_pdf_for_routing(file_path, file_bytes, thresholds)
    _log_routing_decision(decision, filename=log_filename, thresholds=thresholds)

    try:
        if decision.use_ocr:
            # OCR path operates on raw bytes; release the fitz handle now.
            _close_routing_doc(doc)
            doc = None
            return await _run_ocr_service(
                mime_type,
                effective_mode.value,
                file_bytes=file_bytes,
                file_path=file_path,
                user_id=user_id,
            )

        # Hand the already-open doc to TextExtractor so it doesn't reopen.
        # `fitz_doc=None` is safe — TextExtractor falls back to opening its own
        # handle if the classifier was stubbed out (test seam).
        try:
            return await TextExtractor(config_manager=config_manager).extract_text(**extractor_kwargs, fitz_doc=doc)
        except Exception as exc:
            # Classifier said this PDF had real text but extraction failed
            # (corrupt text layer, parser bug, etc.). Pre-SHU-728 the
            # equivalent path silently rerouted to OCR; we preserve that
            # recovery but emit a WARNING so a sustained uptick is visible.
            logger.warning(
                "ocr_routing.text_extraction_failed_falling_back_to_ocr",
                extra={
                    "doc_path": log_filename,
                    "classifier_decision": "text",
                    "classifier_reason": decision.reason,
                    "exception_type": type(exc).__name__,
                    "exception_msg": str(exc),
                },
            )
            _close_routing_doc(doc)
            doc = None
            return await _run_ocr_service(
                mime_type,
                effective_mode.value,
                file_bytes=file_bytes,
                file_path=file_path,
                user_id=user_id,
            )
    finally:
        if doc is not None:
            _close_routing_doc(doc)


async def _classify_pdf_for_routing(
    file_path: str | None,
    file_bytes: bytes | None,
    thresholds: RoutingThresholds,
) -> tuple[RoutingDecision, object | None]:
    """Open the PDF, run the classifier, return ``(decision, open_doc)``.

    Returns the open ``fitz.Document`` so the caller can hand it off to
    ``TextExtractor`` and avoid a second open. The caller is responsible for
    closing the doc (via ``_close_routing_doc``) once it's done — either
    immediately, when routing to OCR, or after text extraction returns.

    The CPU-bound classifier scan runs on the thread-pool executor so the
    event loop stays responsive while fitz/MuPDF do their work.

    This function is the test seam for stubbing the routing decision in
    unit tests. Tests typically patch it to return ``(stub_decision, None)``;
    a ``None`` doc tells the orchestrator there's no handle to hand off, and
    ``TextExtractor`` falls back to opening its own.
    """
    import asyncio

    doc = fitz.open(file_path) if file_path is not None else fitz.open(stream=file_bytes, filetype="pdf")
    loop = asyncio.get_running_loop()
    try:
        decision = await loop.run_in_executor(None, classify_pdf, doc, thresholds)
    except BaseException:
        # Classifier raised (corrupt PDF, fitz internal, etc.). The caller
        # only takes ownership of `doc` on the success path, so close it
        # here to avoid leaking the MuPDF native handle and to fire the
        # store_shrink that `_close_routing_doc` does in its finally.
        _close_routing_doc(doc)
        raise
    return decision, doc


def _close_routing_doc(doc: object | None) -> None:
    """Close a `fitz.Document` opened by the routing classifier.

    Matches the lifecycle contract of `processors.text_extractor._open_pdf`
    by also calling `fitz.TOOLS.store_shrink(100)` to evict cached fonts and
    images from the closed doc — keeps the MuPDF process-global store from
    staircasing toward its cap (SHU-710).
    """
    if doc is None:
        return
    try:
        doc.close()  # type: ignore[attr-defined]
    finally:
        try:
            fitz.TOOLS.store_shrink(100)
        except Exception:
            # Best-effort, matching `_open_pdf`'s contract.
            pass


def _log_routing_decision(decision: RoutingDecision, *, filename: str, thresholds: RoutingThresholds) -> None:
    """Emit the per-document routing log lines.

    INFO carries the summary that's enough to spot a misroute in operator
    logs (decision, reason, page count, fraction, configured thresholds).
    DEBUG carries the per-page signal vector for replay-free diagnosis.

    NOTE: do not use the key `filename` in the `extra` dict — it collides with
    the reserved `LogRecord.filename` attribute (the source file of the log
    call). Use `doc_path` instead.
    """
    summary = {
        "doc_path": filename,
        "decision": "ocr" if decision.use_ocr else "text",
        "reason": decision.reason,
        "page_count": decision.page_count,
        "real_text_fraction": round(decision.real_text_fraction, 4),
        "page_margin_ratio": thresholds.page_margin_ratio,
        "text_page_fraction": thresholds.text_page_fraction,
    }
    logger.info("ocr_routing.auto_decision", extra=summary)
    if decision.pages and logger.isEnabledFor(10):  # logging.DEBUG = 10
        logger.debug(
            "ocr_routing.per_page_signals",
            extra={
                "doc_path": filename,
                "pages": [asdict(p) for p in decision.pages],
            },
        )


async def _run_ocr_service(
    mime_type: str,
    ocr_mode: str,
    *,
    file_bytes: bytes | None = None,
    file_path: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Call the configured OCR service and return a result dict with timing.

    Forwards whichever of ``file_bytes`` / ``file_path`` the caller supplied
    to the provider — does NOT pre-load bytes from disk. ExternalOCRService
    streams the file base64-encoded directly to the Mistral request body
    (SHU-738); LocalOCRService passes the path through to the mmap-backed
    fitz path. The pre-SHU-738 ``_read_file_sync`` step that materialized
    the entire document in Python memory before the provider call is gone.
    """
    import time

    if (file_bytes is None) == (file_path is None):
        raise ValueError("Provide exactly one of file_bytes or file_path")

    start = time.monotonic()
    ocr_service = get_ocr_service()
    ocr_result = await ocr_service.extract_text(
        file_bytes=file_bytes,
        file_path=file_path,
        mime_type=mime_type,
        user_id=user_id,
    )
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
