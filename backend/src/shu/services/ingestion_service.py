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

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..knowledge.ko import deterministic_ko_id
from ..models.document import Document
from ..services.document_service import DocumentService

if TYPE_CHECKING:
    from ..core.queue_backend import QueueBackend

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


async def _trigger_profiling_if_enabled(document_id: str) -> None:
    """Trigger async document profiling if enabled (SHU-344).

    Enqueues a profiling job to the QueueBackend with PROFILING WorkloadType.
    The job will be processed by a worker consuming from the profiling queue.

    Benefits over asyncio.create_task():
    - Jobs persist across server restarts (with Redis backend)
    - Visibility into queue depth and progress
    - Automatic retry with exponential backoff on transient failures
    - Horizontal scaling across multiple worker replicas
    - Concurrency control via worker pool size instead of semaphore

    Does not block the caller - ingestion returns immediately.
    """
    from ..core.config import get_settings_instance

    settings = get_settings_instance()
    if not settings.enable_document_profiling:
        return

    # Import queue backend dependencies
    from ..core.queue_backend import get_queue_backend
    from ..core.workload_routing import WorkloadType, enqueue_job

    try:
        # Get the queue backend
        backend = await get_queue_backend()

        # Enqueue profiling job with PROFILING WorkloadType
        # Use higher max_attempts and longer visibility_timeout for LLM calls
        job = await enqueue_job(
            backend,
            WorkloadType.PROFILING,
            payload={
                "document_id": document_id,
                "action": "profile_document",
            },
            max_attempts=5,  # Retry up to 5 times for transient failures
            visibility_timeout=600,  # 10 minutes for LLM API calls
        )

        logger.info(
            "Document profiling job enqueued",
            extra={
                "document_id": document_id,
                "job_id": job.id,
                "queue_name": job.queue_name,
            },
        )
    except Exception as e:
        # Log error but don't fail ingestion if profiling enqueue fails
        logger.error(
            "Failed to enqueue profiling job: document_id=%s error=%s",
            document_id,
            str(e),
        )


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


def _check_skip(
    existing: Document | None,
    source_hash: str | None,
    content_hash: str,
    force_reingest: bool,
    ko_id: str,
) -> dict[str, Any] | None:
    """Check if document ingestion should be skipped due to unchanged content.

    Returns skip result dict if document should be skipped, None otherwise.

    Only skips if:
    - Document exists
    - force_reingest is False
    - Hash matches (source_hash or content_hash)
    - Document is in terminal successful state (is_ready or is_processed)
    """
    if existing is None or force_reingest:
        return None

    # Check hash match - prefer source_hash, fall back to content_hash
    hash_matches = False

    if source_hash and existing.source_hash:
        hash_matches = source_hash == existing.source_hash
    elif existing.content_hash:
        hash_matches = content_hash == existing.content_hash

    # Only skip if hash matches AND document is in terminal successful state
    if hash_matches and existing.is_processed:
        return {
            "ko_id": ko_id,
            "document_id": existing.id,
            "status": existing.processing_status,
            "word_count": existing.word_count or 0,
            "character_count": existing.character_count or 0,
            "chunk_count": existing.chunk_count or 0,
            "skipped": True,
            "skip_reason": "hash_match",
        }

    # Also skip ERROR-state documents with matching hash — the error is deterministic
    # (same content will fail the same way). Prevents an automatic retry loop on every
    # feed sync. Users can force re-ingestion by re-uploading or setting force_reingest.
    if hash_matches and existing.has_error:
        return {
            "ko_id": ko_id,
            "document_id": existing.id,
            "status": existing.processing_status,
            "word_count": existing.word_count or 0,
            "character_count": existing.character_count or 0,
            "chunk_count": existing.chunk_count or 0,
            "skipped": True,
            "skip_reason": "hash_match_error_state",
        }

    return None


async def _enqueue_embed_job(queue: QueueBackend, document_id: str, knowledge_base_id: str) -> None:
    """Enqueue an embedding job for a document.

    Args:
        queue: QueueBackend instance
        document_id: Document ID to embed
        knowledge_base_id: Knowledge base ID

    """
    from ..core.workload_routing import WorkloadType, enqueue_job

    await enqueue_job(
        queue,
        WorkloadType.INGESTION_EMBED,
        payload={
            "document_id": document_id,
            "knowledge_base_id": knowledge_base_id,
            "action": "embed_document",
        },
        max_attempts=3,
        visibility_timeout=300,
    )


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
    # Only skip if document is in a terminal successful state (PROCESSED).
    # Documents that are PENDING, in-progress, or ERROR should be re-processed.
    if not force_reingest:
        match, matched_hash = _hashes_match(source_hash, content_hash, existing)
        if match and existing.is_processed:
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
        if match and existing.has_error:
            logger.info(
                "Skipping ERROR-state document with matching hash (deterministic failure)",
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
                skip_reason="hash_match_error_state",
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


# TODO: Refactor this function. It's too complex (number of branches and statements).
async def ingest_document(  # noqa: PLR0915
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
    staging_ttl: int | None = None,
) -> dict[str, Any]:
    """Ingest a document asynchronously via queue pipeline.

    Creates Document record with PENDING status and enqueues OCR job.
    Returns immediately without waiting for processing.

    The pipeline stages are:
    1. PENDING → EXTRACTING: OCR job extracts text
    2. EXTRACTING → EMBEDDING: Embed job creates chunks and embeddings
    3. EMBEDDING → PROFILING/PROCESSED: Profiling job (if enabled) or done

    Args:
        db: Database session.
        knowledge_base_id: Target knowledge base ID.
        plugin_name: Name of the plugin ingesting the document.
        user_id: User ID performing the ingestion.
        file_bytes: Raw file bytes to ingest.
        filename: Original filename.
        mime_type: MIME type of the file.
        source_id: Unique source identifier.
        source_url: Optional source URL.
        attributes: Optional additional attributes.
        ocr_mode: Optional OCR mode override.
        staging_ttl: Optional TTL for staged files (defaults to settings.file_staging_ttl).

    """
    from ..core.cache_backend import get_cache_backend
    from ..core.queue_backend import get_queue_backend
    from ..core.workload_routing import WorkloadType, enqueue_job
    from ..models.document import DocumentStatus
    from ..schemas.document import DocumentCreate
    from .file_staging_service import FileStagingService

    # Check for existing document with same source_id (for hash-based skip)
    svc = DocumentService(db)
    existing = await svc.get_document_by_source_id(knowledge_base_id, source_id)

    attrs = attributes or {}
    source_hash = attrs.get("source_hash")
    force_reingest = bool(attrs.get("force_reingest"))

    # Check if we should skip due to unchanged content
    # For ingest_document, we only have source_hash (no content yet), so pass empty content_hash
    ko_id = deterministic_ko_id(f"{plugin_name}:{user_id}", source_id)
    skip_result = _check_skip(existing, source_hash, "", force_reingest, ko_id)
    if skip_result:
        logger.info(
            "Skipping unchanged document",
            extra={
                "kb_id": knowledge_base_id,
                "source_id": source_id,
                "hash": source_hash,
                "document_id": existing.id,
            },
        )
        return skip_result

    # Create document record with PENDING status and empty content
    source_type = f"plugin:{plugin_name}"
    file_type = _infer_file_type(filename, mime_type)
    title = filename or source_id
    source_modified_at = _safe_dt(attrs.get("modified_at"))
    effective_source_url = source_url or attrs.get("source_url")

    if existing is None:
        # New document - create it with PENDING status
        doc_create = DocumentCreate(
            knowledge_base_id=knowledge_base_id,
            title=title,
            file_type=file_type,
            source_type=source_type,
            source_id=source_id,
            source_url=effective_source_url,
            file_size=len(file_bytes) if file_bytes else None,
            content="",  # Will be populated by OCR handler
            content_hash=None,
            source_hash=source_hash,
            source_modified_at=source_modified_at,
        )
        created = await svc.create_document(doc_create)
        from sqlalchemy import select

        res = await db.execute(select(Document).where(Document.id == created.id))
        document = res.scalar_one()
        # Set status to PENDING atomically with creation
        document.update_status(DocumentStatus.PENDING)
        await db.commit()
    else:
        # Existing document - update for re-ingestion with PENDING status
        document = existing
        document.title = title
        document.file_type = file_type
        document.source_type = source_type
        document.file_size = len(file_bytes) if file_bytes else None
        document.content = ""  # Will be populated by OCR handler
        document.content_hash = None
        if effective_source_url:
            document.source_url = effective_source_url
        if source_hash:
            document.source_hash = source_hash
        if source_modified_at is not None:
            document.source_modified_at = source_modified_at
        # Clear stale extraction metadata so previous OCR results don't persist
        document.extraction_method = None
        document.extraction_engine = None
        document.extraction_confidence = None
        document.extraction_duration = None
        document.extraction_metadata = None
        # Set status to PENDING atomically with update
        document.update_status(DocumentStatus.PENDING)
        db.add(document)
        await db.commit()
        await db.refresh(document)

    # Stage file bytes and enqueue OCR job
    staging_key = None
    staging_service = None
    try:
        cache = await get_cache_backend()
        staging_service = (
            FileStagingService(cache, staging_ttl=staging_ttl) if staging_ttl is not None else FileStagingService(cache)
        )
        staging_key = await staging_service.stage_file(document.id, file_bytes)

        # Enqueue OCR job
        queue = await get_queue_backend()
        job_payload = {
            "document_id": document.id,
            "knowledge_base_id": knowledge_base_id,
            "filename": filename,
            "mime_type": mime_type,
            "source_id": source_id,
            "staging_key": staging_key,
            "ocr_mode": ocr_mode,
            "action": "extract_text",
        }

        await enqueue_job(
            queue,
            WorkloadType.INGESTION_OCR,
            payload=job_payload,
            max_attempts=3,
            visibility_timeout=600,  # 10 minutes for large PDFs
        )
    except Exception as e:
        logger.error(
            "Failed to stage/enqueue document ingestion",
            extra={
                "document_id": document.id,
                "knowledge_base_id": knowledge_base_id,
                "error": str(e),
            },
        )
        # Mark document as ERROR so it doesn't stay PENDING forever
        document.update_status(DocumentStatus.ERROR)
        document.processing_error = f"Failed to stage/enqueue: {e}"
        db.add(document)
        await db.commit()
        # Clean up staged bytes if staging succeeded but enqueue failed
        if staging_key and staging_service:
            await staging_service.delete_staged_file(staging_key)
        raise

    logger.info(
        "Document ingestion enqueued",
        extra={
            "document_id": document.id,
            "knowledge_base_id": knowledge_base_id,
            "source_id": source_id,
            "staging_key": staging_key,
        },
    )

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
        "document_id": document.id,
        "status": DocumentStatus.PENDING.value,
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

    try:
        word_count, character_count, chunk_count = await svc.process_and_update_chunks(
            knowledge_base_id,
            document,
            title,
            content,
        )
    except Exception as e:
        logger.error(
            "Failed to process email document chunks",
            extra={
                "document_id": document.id,
                "knowledge_base_id": knowledge_base_id,
                "error": str(e),
            },
        )
        document.mark_error(f"Chunk processing failed: {e}")
        db.add(document)
        await db.commit()
        raise

    # Trigger async profiling if enabled
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


# TODO: Refactor this function. It's too complex (number of branches and statements).
async def ingest_text(  # noqa: PLR0915
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
    """Ingest text content directly via queue pipeline (no OCR needed).

    Creates Document with content and enqueues EMBED job directly,
    skipping the OCR stage. Returns immediately without waiting for processing.

    The pipeline stages are:
    1. EMBEDDING: Embed job creates chunks and embeddings
    2. EMBEDDING → PROFILING/PROCESSED: Profiling job (if enabled) or done
    """
    from ..core.queue_backend import get_queue_backend
    from ..models.document import DocumentStatus
    from ..schemas.document import DocumentCreate

    svc = DocumentService(db)
    source_type = f"plugin:{plugin_name}"
    file_type = "txt"
    effective_title = title or source_id
    effective_content = content or ""

    attrs = attributes or {}
    source_hash = attrs.get("source_hash")
    force_reingest = bool(attrs.get("force_reingest"))

    # Compute content hash once for skip check and document creation
    content_hash = hashlib.sha256(effective_content.encode("utf-8")).hexdigest()

    # Check for existing document with same source_id (for hash-based skip)
    existing = await svc.get_document_by_source_id(knowledge_base_id, source_id)

    # Check if we should skip due to unchanged content
    ko_id = deterministic_ko_id(f"{plugin_name}:{user_id}", source_id)
    skip_result = _check_skip(existing, source_hash, content_hash, force_reingest, ko_id)
    if skip_result:
        logger.info(
            "Skipping unchanged document",
            extra={
                "kb_id": knowledge_base_id,
                "source_id": source_id,
                "hash": content_hash,
                "document_id": existing.id,
            },
        )
        return skip_result

    source_modified_at = _safe_dt(attrs.get("modified_at"))
    effective_source_url = source_url or attrs.get("source_url")

    if existing is None:
        # New document - create it with content populated
        doc_create = DocumentCreate(
            knowledge_base_id=knowledge_base_id,
            title=effective_title,
            file_type=file_type,
            source_type=source_type,
            source_id=source_id,
            source_url=effective_source_url,
            file_size=len(effective_content) if effective_content else None,
            content=effective_content,
            content_hash=content_hash,
            source_hash=source_hash,
            source_modified_at=source_modified_at,
            extraction_method="text",
            extraction_engine="direct",
        )
        created = await svc.create_document(doc_create)
        from sqlalchemy import select

        res = await db.execute(select(Document).where(Document.id == created.id))
        document = res.scalar_one()
    else:
        # Existing document - update for re-ingestion
        document = existing
        document.title = effective_title
        document.file_type = file_type
        document.source_type = source_type
        document.file_size = len(effective_content) if effective_content else None
        document.content = effective_content
        document.content_hash = content_hash
        document.extraction_method = "text"
        document.extraction_engine = "direct"
        # Clear stale extraction metadata from previous ingestion (e.g. OCR)
        document.extraction_confidence = None
        document.extraction_duration = None
        document.extraction_metadata = None
        if effective_source_url:
            document.source_url = effective_source_url
        if source_hash:
            document.source_hash = source_hash
        if source_modified_at is not None:
            document.source_modified_at = source_modified_at
        db.add(document)
        await db.commit()
        await db.refresh(document)

    # Enqueue embedding job directly (no file staging needed).
    # Commit EMBEDDING status only after enqueue succeeds to avoid a window
    # where the document is EMBEDDING with no job in the queue.
    try:
        queue = await get_queue_backend()
        await _enqueue_embed_job(queue, document.id, knowledge_base_id)
    except Exception as e:
        logger.error(
            "Failed to enqueue text ingestion embedding job",
            extra={
                "document_id": document.id,
                "knowledge_base_id": knowledge_base_id,
                "error": str(e),
            },
        )
        document.update_status(DocumentStatus.ERROR)
        document.processing_error = f"Failed to enqueue embedding: {e}"
        db.add(document)
        await db.commit()
        raise

    document.update_status(DocumentStatus.EMBEDDING)
    await db.commit()

    logger.info(
        "Text ingestion enqueued (skipping OCR)",
        extra={
            "document_id": document.id,
            "knowledge_base_id": knowledge_base_id,
            "source_id": source_id,
        },
    )

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", source_id),
        "document_id": document.id,
        "status": DocumentStatus.EMBEDDING.value,
        "skipped": False,
    }


# TODO: Refactor this function. It's too complex (number of branches and statements).
async def ingest_thread(  # noqa: PLR0915
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
    """Ingest thread content directly via queue pipeline (no OCR needed).

    Creates Document with content and enqueues EMBED job directly,
    skipping the OCR stage. Returns immediately without waiting for processing.

    The pipeline stages are:
    1. EMBEDDING: Embed job creates chunks and embeddings
    2. EMBEDDING → PROFILING/PROCESSED: Profiling job (if enabled) or done
    """
    from ..core.queue_backend import get_queue_backend
    from ..models.document import DocumentStatus
    from ..schemas.document import DocumentCreate

    svc = DocumentService(db)
    source_type = f"plugin:{plugin_name}"
    file_type = "thread"
    effective_title = title or thread_id
    effective_content = content or ""

    attrs = attributes or {}
    source_hash = attrs.get("source_hash")
    force_reingest = bool(attrs.get("force_reingest"))

    # Compute content hash once for skip check and document creation
    content_hash = hashlib.sha256(effective_content.encode("utf-8")).hexdigest()

    # Check for existing document with same source_id (for hash-based skip)
    existing = await svc.get_document_by_source_id(knowledge_base_id, thread_id)

    # Check if we should skip due to unchanged content
    ko_id = deterministic_ko_id(f"{plugin_name}:{user_id}", thread_id)
    skip_result = _check_skip(existing, source_hash, content_hash, force_reingest, ko_id)
    if skip_result:
        logger.info(
            "Skipping unchanged thread",
            extra={
                "kb_id": knowledge_base_id,
                "thread_id": thread_id,
                "hash": content_hash,
                "document_id": existing.id,
            },
        )
        return skip_result

    source_modified_at = _safe_dt(attrs.get("modified_at"))
    effective_source_url = source_url or attrs.get("source_url")

    if existing is None:
        # New document - create it with content populated
        doc_create = DocumentCreate(
            knowledge_base_id=knowledge_base_id,
            title=effective_title,
            file_type=file_type,
            source_type=source_type,
            source_id=thread_id,
            source_url=effective_source_url,
            file_size=len(effective_content) if effective_content else None,
            content=effective_content,
            content_hash=content_hash,
            source_hash=source_hash,
            source_modified_at=source_modified_at,
            extraction_method="text",
            extraction_engine="direct",
        )
        created = await svc.create_document(doc_create)
        from sqlalchemy import select

        res = await db.execute(select(Document).where(Document.id == created.id))
        document = res.scalar_one()
    else:
        # Existing document - update for re-ingestion
        document = existing
        document.title = effective_title
        document.file_type = file_type
        document.source_type = source_type
        document.file_size = len(effective_content) if effective_content else None
        document.content = effective_content
        document.content_hash = content_hash
        document.extraction_method = "text"
        document.extraction_engine = "direct"
        # Clear stale extraction metadata from previous ingestion (e.g. OCR)
        document.extraction_confidence = None
        document.extraction_duration = None
        document.extraction_metadata = None
        if effective_source_url:
            document.source_url = effective_source_url
        if source_hash:
            document.source_hash = source_hash
        if source_modified_at is not None:
            document.source_modified_at = source_modified_at
        db.add(document)
        await db.commit()
        await db.refresh(document)

    # Enqueue embedding job directly (no file staging needed).
    # Commit EMBEDDING status only after enqueue succeeds to avoid a window
    # where the document is EMBEDDING with no job in the queue.
    try:
        queue = await get_queue_backend()
        await _enqueue_embed_job(queue, document.id, knowledge_base_id)
    except Exception as e:
        logger.error(
            "Failed to enqueue thread ingestion embedding job",
            extra={
                "document_id": document.id,
                "knowledge_base_id": knowledge_base_id,
                "error": str(e),
            },
        )
        document.update_status(DocumentStatus.ERROR)
        document.processing_error = f"Failed to enqueue embedding: {e}"
        db.add(document)
        await db.commit()
        raise

    document.update_status(DocumentStatus.EMBEDDING)
    await db.commit()

    logger.info(
        "Thread ingestion enqueued (skipping OCR)",
        extra={
            "document_id": document.id,
            "knowledge_base_id": knowledge_base_id,
            "thread_id": thread_id,
        },
    )

    return {
        "ko_id": deterministic_ko_id(f"{plugin_name}:{user_id}", thread_id),
        "document_id": document.id,
        "status": DocumentStatus.EMBEDDING.value,
        "skipped": False,
    }
