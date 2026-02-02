"""High-level ingestion helpers used by host.kb capability.

Implementation Status: Partial
- Currently implements ingest_document, ingest_email, ingest_text, ingest_thread
- Reuses TextExtractor + DocumentService + RAGProcessingService

Limitations/Known Issues:
- Streaming inputs are not yet supported directly; callers pass bytes. The legacy processor can be wired later for streaming/storage refs.
- Email/thread lineage for attachments/messages is not yet modeled beyond attributes linking; add relational linkage if needed.
- Source URL construction for providers (e.g., Gmail message deep links) is left to callers.

Security Vulnerabilities:
- Callers must ensure redaction/PII policies before passing content to ingestion.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..knowledge.ko import deterministic_ko_id
from ..models.document import Document
from ..processors.text_extractor import TextExtractor, UnsupportedFileFormatError
from ..services.document_service import DocumentService

logger = get_logger(__name__)


@dataclass
class UpsertResult:
    """Result of document upsert operation."""

    document: Document
    extraction: dict[str, Any]
    skipped: bool
    skip_reason: str | None = None


def _build_skipped_result(
    document: Document,
    extraction: dict[str, Any],
    ko_id: str,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    """Build a result dict for a skipped (unchanged) document.

    Centralizes the skip-result pattern to avoid duplication across ingestion functions.
    """
    return {
        "ko_id": ko_id,
        "document_id": document.id,
        "word_count": document.word_count or 0,
        "character_count": document.character_count or 0,
        "chunk_count": document.chunk_count or 0,
        "extraction": extraction,
        "skipped": True,
        "skip_reason": skip_reason,
    }


# Module-level semaphore for limiting concurrent profiling tasks.
# Prevents LLM rate-limit storms during bulk imports. Tasks beyond this limit
# queue in memory until a slot opens. See SHU-211 for persistent queue migration.
_profiling_semaphore: asyncio.Semaphore | None = None


def _get_profiling_semaphore() -> asyncio.Semaphore:
    """Get or create the profiling semaphore with configured concurrency limit."""
    global _profiling_semaphore
    if _profiling_semaphore is None:
        from ..core.config import get_settings_instance

        settings = get_settings_instance()
        _profiling_semaphore = asyncio.Semaphore(settings.profiling_max_concurrent_tasks)
    return _profiling_semaphore


async def _trigger_profiling_if_enabled(document_id: str) -> None:
    """Trigger async document profiling if enabled (SHU-344).

    This spawns a fire-and-forget background task that:
    1. Acquires a slot from the profiling semaphore (waits if at capacity)
    2. Opens a new DB session
    3. Calls the profiling orchestrator
    4. Updates document/chunk records with profile data

    Does not block the caller - ingestion returns immediately.
    Must be called from an async context to ensure event loop exists.

    Note: Tasks queue in memory if concurrency limit is reached. Tasks are lost
    on server restart. See SHU-211 for migration to persistent work queue.
    """
    from ..core.config import get_settings_instance

    settings = get_settings_instance()
    if not settings.enable_document_profiling:
        return

    semaphore = _get_profiling_semaphore()

    async def _run_profiling() -> None:
        async with semaphore:
            try:
                from ..core.config import get_config_manager
                from ..core.database import get_async_session_local
                from .profiling_orchestrator import ProfilingOrchestrator
                from .side_call_service import SideCallService

                session_local = get_async_session_local()
                async with session_local() as bg_session:
                    config_manager = get_config_manager()
                    side_call_service = SideCallService(bg_session, config_manager)
                    orchestrator = ProfilingOrchestrator(bg_session, settings, side_call_service)
                    result = await orchestrator.run_for_document(document_id)
                    if result.success:
                        logger.info(
                            "Document profiling complete: document_id=%s mode=%s tokens_used=%s duration_ms=%s",
                            document_id,
                            result.profiling_mode.value,
                            result.tokens_used,
                            result.duration_ms,
                        )
                    else:
                        logger.warning(
                            "Document profiling failed: document_id=%s error=%s",
                            document_id,
                            result.error,
                        )
            except Exception as e:
                logger.error(
                    "Document profiling error: document_id=%s error=%s",
                    document_id,
                    str(e),
                )

    # Fire-and-forget - don't await
    asyncio.create_task(_run_profiling())


def _infer_file_type(filename: str, mime_type: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or mime_type == "application/pdf":
        return "pdf"
    for ext in (
        ".md",
        ".txt",
        ".docx",
        ".doc",
        ".rtf",
        ".html",
        ".htm",
        ".eml",
        ".csv",
        ".py",
        ".js",
        ".xlsx",
        ".pptx",
    ):
        if name.endswith(ext):
            return ext.lstrip(".")
    if mime_type in (
        "text/plain",
        "text/markdown",
        "text/html",
        "text/csv",
        # JavaScript
        "text/javascript",
        "application/javascript",
        "application/x-javascript",
        "text/ecmascript",
        "application/ecmascript",
        # Python
        "text/x-python",
        "application/x-python",
        "application/x-python-code",
    ):
        if mime_type == "text/plain":
            return "txt"
        if mime_type == "text/markdown":
            return "md"
        if mime_type == "text/html":
            return "html"
        if mime_type == "text/csv":
            return "csv"
        if mime_type in (
            "text/javascript",
            "application/javascript",
            "application/x-javascript",
            "text/ecmascript",
            "application/ecmascript",
        ):
            return "js"
        if mime_type in ("text/x-python", "application/x-python", "application/x-python-code"):
            return "py"
        return "txt"
    return "txt"


def _safe_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def _hashes_match(incoming_source_hash: str | None, incoming_content_hash: str, existing: Document) -> tuple[bool, str]:
    """Compare incoming hashes with existing document hashes.

    Priority: source_hash (provider-supplied, e.g., md5Checksum) > content_hash (computed SHA256).

    Returns:
        Tuple of (match: bool, matched_hash: str for logging, empty if no comparison possible)

    """
    # Prefer source_hash comparison if both sides have it
    if incoming_source_hash and existing.source_hash:
        return (incoming_source_hash == existing.source_hash, incoming_source_hash)

    # Fall back to content_hash comparison
    if existing.content_hash:
        return (incoming_content_hash == existing.content_hash, incoming_content_hash)

    # No existing hash to compare against - cannot skip
    return (False, "")


async def _upsert_document_record(
    svc: DocumentService,
    knowledge_base_id: str,
    *,
    source_id: str,
    source_type: str,
    title: str,
    file_type: str,
    content: str,
    extraction: dict[str, Any],
    source_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> UpsertResult:
    """Create or update a document record.

    If the document already exists and hashes match (unchanged content),
    returns skipped=True to signal callers to skip chunk recomputation.

    Use force_reingest=True in attributes to bypass hash check.
    """
    attrs = attributes or {}
    normalized_content = content or ""
    normalized_title = title or source_id
    content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    file_size = len(normalized_content) if normalized_content else None

    extraction_data = dict(extraction or {})
    if not extraction_data.get("details") and attrs.get("extraction_metadata"):
        extraction_data["details"] = attrs.get("extraction_metadata")

    source_hash = attrs.get("source_hash")
    source_modified_at = _safe_dt(attrs.get("modified_at"))
    effective_source_url = source_url or attrs.get("source_url")
    force_reingest = bool(attrs.get("force_reingest"))

    existing = await svc.get_document_by_source_id(knowledge_base_id, source_id)

    if existing is None:
        # New document - create it
        from ..schemas.document import DocumentCreate

        doc_create = DocumentCreate(
            knowledge_base_id=knowledge_base_id,
            title=normalized_title,
            file_type=file_type,
            source_type=source_type,
            source_id=source_id,
            source_url=effective_source_url,
            file_size=file_size,
            content=normalized_content,
            content_hash=content_hash,
            source_hash=source_hash,
            source_modified_at=source_modified_at,
            extraction_method=extraction_data.get("method"),
            extraction_engine=extraction_data.get("engine"),
            extraction_confidence=extraction_data.get("confidence"),
            extraction_duration=extraction_data.get("duration"),
            extraction_metadata=extraction_data.get("details"),
        )
        created = await svc.create_document(doc_create)
        from sqlalchemy import select

        res = await svc.db.execute(select(Document).where(Document.id == created.id))
        document = res.scalar_one()
        return UpsertResult(document=document, extraction=extraction_data, skipped=False)

    # Existing document - check if content is unchanged
    if not force_reingest:
        match, matched_hash = _hashes_match(source_hash, content_hash, existing)
        if match:
            logger.info(
                "Skipping unchanged document",
                extra={
                    "kb_id": knowledge_base_id,
                    "source_type": source_type,
                    "source_id": source_id,
                    "hash": matched_hash,
                    "document_id": existing.id,
                },
            )
            return UpsertResult(
                document=existing,
                extraction=extraction_data,
                skipped=True,
                skip_reason="hash_match",
            )

    # Content changed or force_reingest - update the document
    document = existing
    document.title = normalized_title
    document.file_type = file_type
    document.source_type = source_type
    document.source_id = source_id
    document.content = normalized_content
    document.content_hash = content_hash
    document.file_size = file_size
    if effective_source_url:
        document.source_url = effective_source_url
    if source_hash:
        document.source_hash = source_hash
    if source_modified_at is not None:
        document.source_modified_at = source_modified_at
    document.extraction_method = extraction_data.get("method")
    document.extraction_engine = extraction_data.get("engine")
    document.extraction_confidence = extraction_data.get("confidence")
    document.extraction_duration = extraction_data.get("duration")
    document.extraction_metadata = extraction_data.get("details")
    svc.db.add(document)
    await svc.db.commit()
    await svc.db.refresh(document)

    return UpsertResult(document=document, extraction=extraction_data, skipped=False)


async def ingest_document(
    db: AsyncSession,
    knowledge_base_id: str,
    *,
    plugin_name: str,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    source_id: str,
    source_url: str | None = None,
    attributes: dict[str, Any] | None = None,
    ocr_mode: str | None = None,
) -> dict[str, Any]:
    # OCR/text extraction
    mt = (mime_type or "").lower()
    # Prefer filename extension when available; allow caller hint via attributes; fall back to MIME type
    fname = filename or ""
    ext_from_name = None
    if isinstance(fname, str) and "." in fname:
        ext_from_name = "." + fname.rsplit(".", 1)[-1].lower()

    ext_map = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/csv": ".csv",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "message/rfc822": ".eml",
        # JavaScript mimetypes
        "text/javascript": ".js",
        "application/javascript": ".js",
        "application/x-javascript": ".js",
        "text/ecmascript": ".js",
        "application/ecmascript": ".js",
        # Common Python mimetypes observed in the wild
        "text/x-python": ".py",
        "application/x-python": ".py",
        "application/x-python-code": ".py",
        # Office
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        # XML
        "text/xml": ".xml",
        "application/xml": ".xml",
    }

    # Optional caller hint via attributes: preferred_extension or force_extension
    preferred_ext = None
    try:
        if attributes and isinstance(attributes, dict):
            preferred_ext = attributes.get("preferred_extension") or attributes.get("force_extension")
            if preferred_ext and not str(preferred_ext).startswith("."):
                preferred_ext = f".{preferred_ext}"
    except Exception:
        preferred_ext = None

    # Choose suffix: caller hint > filename extension > MIME mapping > generic text/* -> .txt > .bin
    suffix = preferred_ext or ext_from_name or ext_map.get(mt) or (".txt" if mt.startswith("text/") else ".bin")
    file_path = f"/virtual/input{suffix}"
    extractor = TextExtractor()
    eff = (ocr_mode or "auto").strip().lower()
    if eff not in {"auto", "always", "never", "fallback"}:
        eff = "auto"
    try:
        if eff == "fallback":
            res_no = await extractor.extract_text(
                file_path,
                file_content=file_bytes,
                use_ocr=False,
                kb_config=None,
                progress_context=None,
            )
            text = ""
            meta: dict[str, Any] = {}
            if isinstance(res_no, dict):
                text = str(res_no.get("text") or "")
                meta = res_no.get("metadata") or {}
                if text.strip():
                    extraction = {
                        "method": meta.get("method"),
                        "engine": meta.get("engine"),
                        "confidence": meta.get("confidence"),
                        "duration": meta.get("duration"),
                        "details": meta,
                    }
                else:
                    res_ocr = await extractor.extract_text(
                        file_path,
                        file_content=file_bytes,
                        use_ocr=True,
                        kb_config=None,
                        progress_context=None,
                    )
                    if isinstance(res_ocr, dict):
                        text = str(res_ocr.get("text") or "")
                        meta = res_ocr.get("metadata") or {}
                    else:
                        text = str(res_ocr or "")
                        meta = {}
                    extraction = {
                        "method": meta.get("method"),
                        "engine": meta.get("engine"),
                        "confidence": meta.get("confidence"),
                        "duration": meta.get("duration"),
                        "details": meta,
                    }
            else:
                text = str(res_no or "")
                if not text.strip():
                    res_ocr = await extractor.extract_text(
                        file_path,
                        file_content=file_bytes,
                        use_ocr=True,
                        kb_config=None,
                        progress_context=None,
                    )
                    if isinstance(res_ocr, dict):
                        text = str(res_ocr.get("text") or "")
                        meta = res_ocr.get("metadata") or {}
                    else:
                        text = str(res_ocr or "")
                        meta = {}
                extraction = {
                    "method": meta.get("method"),
                    "engine": meta.get("engine"),
                    "confidence": meta.get("confidence"),
                    "duration": meta.get("duration"),
                    "details": meta,
                }
        else:
            use_ocr = True if eff == "always" else False if eff == "never" else (mt == "application/pdf")
            res = await extractor.extract_text(
                file_path,
                file_content=file_bytes,
                use_ocr=use_ocr,
                kb_config=None,
                progress_context=None,
            )
            if isinstance(res, dict):
                text = str(res.get("text") or "")
                meta = res.get("metadata") or {}
            else:
                text = str(res or "")
                meta = {}
            extraction = {
                "method": meta.get("method"),
                "engine": meta.get("engine"),
                "confidence": meta.get("confidence"),
                "duration": meta.get("duration"),
                "details": meta,
            }
    except UnsupportedFileFormatError:
        # Preserve a useful reason in extraction details for upstream callers
        text = ""
        extraction = {
            "method": None,
            "engine": None,
            "confidence": None,
            "duration": None,
            "details": {"error": "unsupported_format", "file_extension": suffix},
        }
    except Exception:
        text = ""
        extraction = {
            "method": None,
            "engine": None,
            "confidence": None,
            "duration": None,
            "details": {},
        }

    # Persist Document (create or update by kb_id/source_id)
    svc = DocumentService(db)
    source_type = f"plugin:{plugin_name}"
    file_type = _infer_file_type(filename, mime_type)
    title = filename or source_id
    content = text or ""

    upsert_result = await _upsert_document_record(
        svc,
        knowledge_base_id,
        source_id=source_id,
        source_type=source_type,
        title=title,
        file_type=file_type,
        content=content,
        extraction={
            "method": extraction.get("method") or ("ocr" if extraction.get("engine") else "text"),
            "engine": extraction.get("engine") or plugin_name,
            "confidence": extraction.get("confidence"),
            "duration": extraction.get("duration"),
            "details": extraction.get("details"),
        },
        source_url=source_url,
        attributes=attributes,
    )

    document = upsert_result.document
    extraction = upsert_result.extraction

    # Skip chunk recomputation if document is unchanged
    if upsert_result.skipped:
        return _build_skipped_result(
            document,
            extraction,
            deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
            upsert_result.skip_reason,
        )

    word_count, character_count, chunk_count = await svc.process_and_update_chunks(
        knowledge_base_id,
        document,
        title,
        content,
    )

    # Trigger async profiling if enabled (SHU-344)
    await _trigger_profiling_if_enabled(document.id)

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
        "document_id": document.id,
        "word_count": word_count,
        "character_count": character_count,
        "chunk_count": chunk_count,
        "extraction": extraction,
        "skipped": False,
    }


async def ingest_email(
    db: AsyncSession,
    knowledge_base_id: str,
    *,
    plugin_name: str,
    user_id: str,
    subject: str,
    sender: str | None,
    recipients: dict[str, list[str]],
    date: str | None,
    message_id: str,
    thread_id: str | None,
    body_text: str | None,
    body_html: str | None = None,
    labels: list[str] | None = None,
    source_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Build an indexable text body
    header_lines: list[str] = []
    header_lines.append(f"Subject: {subject or '(no subject)'}")
    if sender:
        header_lines.append(f"From: {sender}")
    tos = ", ".join((recipients or {}).get("to", []) or [])
    ccs = ", ".join((recipients or {}).get("cc", []) or [])
    bccs = ", ".join((recipients or {}).get("bcc", []) or [])
    if tos:
        header_lines.append(f"To: {tos}")
    if ccs:
        header_lines.append(f"Cc: {ccs}")
    if bccs:
        header_lines.append(f"Bcc: {bccs}")
    if date:
        header_lines.append(f"Date: {date}")
    header = "\n".join(header_lines)
    body = body_text or ""
    if not body and body_html:
        # very naive strip; caller should prefer body_text
        import re as _re

        s = _re.sub(r"<script[\s\S]*?</script>", " ", body_html, flags=_re.IGNORECASE)
        s = _re.sub(r"<style[\s\S]*?</style>", " ", s, flags=_re.IGNORECASE)
        s = _re.sub(r"<[^>]+>", " ", s)
        s = _re.sub(r"\s+", " ", s)
        body = s.strip()
    content = f"{header}\n\n{body}".strip()

    # Persist Document
    svc = DocumentService(db)
    external_id = str((attributes or {}).get("external_id") or message_id)
    source_type = f"plugin:{plugin_name}"
    file_type = "email"
    title = subject or "(no subject)"

    extraction_details: dict[str, Any] = {
        "external_id": external_id,
        "message_id": message_id,
        "thread_id": thread_id,
        "labels": labels or [],
    }
    if attributes and attributes.get("extraction_metadata"):
        extraction_details.update(attributes.get("extraction_metadata") or {})
    extraction = {
        "method": "text",
        "engine": "direct",
        "confidence": None,
        "duration": None,
        "details": extraction_details,
    }

    upsert_result = await _upsert_document_record(
        svc,
        knowledge_base_id,
        source_id=external_id,
        source_type=source_type,
        title=title,
        file_type=file_type,
        content=content,
        extraction=extraction,
        source_url=source_url,
        attributes=attributes,
    )

    document = upsert_result.document
    extraction = upsert_result.extraction

    # Skip chunk recomputation if document is unchanged
    if upsert_result.skipped:
        return _build_skipped_result(
            document,
            extraction,
            deterministic_ko_id(f"{plugin_name}:{user_id}", external_id),
            upsert_result.skip_reason,
        )

    word_count, character_count, chunk_count = await svc.process_and_update_chunks(
        knowledge_base_id,
        document,
        title,
        content,
    )

    # Trigger async profiling if enabled (SHU-344)
    await _trigger_profiling_if_enabled(document.id)

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", external_id),
        "document_id": document.id,
        "word_count": word_count,
        "character_count": character_count,
        "chunk_count": chunk_count,
        "extraction": extraction,
        "skipped": False,
    }


async def ingest_text(
    db: AsyncSession,
    knowledge_base_id: str,
    *,
    plugin_name: str,
    user_id: str,
    title: str,
    content: str,
    source_id: str,
    source_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    svc = DocumentService(db)
    source_type = f"plugin:{plugin_name}"
    file_type = "txt"
    effective_title = title or source_id
    effective_content = content or ""

    upsert_result = await _upsert_document_record(
        svc,
        knowledge_base_id,
        source_id=source_id,
        source_type=source_type,
        title=effective_title,
        file_type=file_type,
        content=effective_content,
        extraction={
            "method": "text",
            "engine": "direct",
            "confidence": None,
            "duration": None,
            "details": {},
        },
        source_url=source_url,
        attributes=attributes,
    )

    document = upsert_result.document
    extraction = upsert_result.extraction

    # Skip chunk recomputation if document is unchanged
    if upsert_result.skipped:
        return _build_skipped_result(
            document,
            extraction,
            deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
            upsert_result.skip_reason,
        )

    word_count, character_count, chunk_count = await svc.process_and_update_chunks(
        knowledge_base_id,
        document,
        effective_title,
        effective_content,
    )

    # Trigger async profiling if enabled (SHU-344)
    await _trigger_profiling_if_enabled(document.id)

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
        "document_id": document.id,
        "word_count": word_count,
        "character_count": character_count,
        "chunk_count": chunk_count,
        "extraction": extraction,
        "skipped": False,
    }


async def ingest_thread(
    db: AsyncSession,
    knowledge_base_id: str,
    *,
    plugin_name: str,
    user_id: str,
    title: str,
    content: str,
    thread_id: str,
    source_url: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Treat as a text document with file_type 'thread'
    svc = DocumentService(db)
    source_type = f"plugin:{plugin_name}"
    file_type = "thread"
    effective_title = title or thread_id
    effective_content = content or ""

    upsert_result = await _upsert_document_record(
        svc,
        knowledge_base_id,
        source_id=thread_id,
        source_type=source_type,
        title=effective_title,
        file_type=file_type,
        content=effective_content,
        extraction={
            "method": "text",
            "engine": "direct",
            "confidence": None,
            "duration": None,
            "details": {},
        },
        source_url=source_url,
        attributes=attributes,
    )

    document = upsert_result.document
    extraction = upsert_result.extraction

    # Skip chunk recomputation if document is unchanged
    if upsert_result.skipped:
        return _build_skipped_result(
            document,
            extraction,
            deterministic_ko_id(f"{plugin_name}:{user_id}", thread_id),
            upsert_result.skip_reason,
        )

    word_count, character_count, chunk_count = await svc.process_and_update_chunks(
        knowledge_base_id,
        document,
        effective_title,
        effective_content,
    )

    # Trigger async profiling if enabled (SHU-344)
    await _trigger_profiling_if_enabled(document.id)

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", thread_id),
        "document_id": document.id,
        "word_count": word_count,
        "character_count": character_count,
        "chunk_count": chunk_count,
        "extraction": extraction,
        "skipped": False,
    }
