"""Worker Entrypoint Module.

This module provides the command-line entrypoint for running dedicated worker
processes. Workers can be configured to consume specific workload types and
run independently from the API process.

Usage:
    # Run worker consuming all workload types
    python -m shu.worker

    # Run worker consuming specific workload types
    python -m shu.worker --workload-types=INGESTION,PROFILING

    # Run worker with custom poll interval
    python -m shu.worker --workload-types=LLM_WORKFLOW --poll-interval=0.5

Example Docker Compose:
    worker-ingestion:
      image: shu:latest
      command: python -m shu.worker --workload-types=INGESTION
      replicas: 5
      environment:
        - SHU_REDIS_URL=redis://redis:6379

    worker-llm:
      image: shu:latest
      command: python -m shu.worker --workload-types=LLM_WORKFLOW,PROFILING
      replicas: 2
      environment:
        - SHU_REDIS_URL=redis://redis:6379

    # Note: When using dedicated workers, set SHU_WORKERS_ENABLED=false on the API
    # to prevent duplicate job processing.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from .auth.models import User
from .core.config import get_settings_instance
from .core.database import get_async_session_local, init_db
from .core.exceptions import ShuException
from .core.logging import get_logger, setup_logging
from .core.queue_backend import get_queue_backend
from .core.worker import Worker, WorkerConfig
from .core.workload_routing import WorkloadType
from .services.experience_service import ExperienceService
from .services.ingestion_service import _ERR_FILE_STAGING

logger = get_logger(__name__)


def parse_workload_types(workload_types_str: str) -> set[WorkloadType]:
    """Parse comma-separated workload types string into a set of WorkloadType enums.

    Args:
        workload_types_str: Comma-separated string of workload type names
            (e.g., "INGESTION,PROFILING" or "ingestion,profiling").

    Returns:
        Set of WorkloadType enum values.

    Raises:
        ValueError: If any workload type name is invalid.

    Example:
        types = parse_workload_types("INGESTION,PROFILING")
        # Returns: {WorkloadType.INGESTION, WorkloadType.PROFILING}

    """
    if not workload_types_str.strip():
        raise ValueError("Workload types cannot be empty")

    workload_types = set()
    for name in workload_types_str.split(","):
        name = name.strip().upper()  # noqa: PLW2901
        if not name:
            continue

        try:
            workload_type = WorkloadType[name]
            workload_types.add(workload_type)
        except KeyError as err:
            valid_types = [wt.name for wt in WorkloadType]
            raise ValueError(f"Invalid workload type: {name}. Valid types are: {', '.join(valid_types)}") from err

    if not workload_types:
        raise ValueError("At least one workload type must be specified")

    return workload_types


# TODO: Refactor this function. It's too complex (number of branches and statements).
async def _handle_ocr_job(job) -> None:  # noqa: PLR0915
    """Handle an INGESTION_OCR workload job.

    Retrieves file bytes from staging, extracts text using OCR/text extraction,
    updates the Document with content and extraction metadata, and enqueues
    the next pipeline stage (INGESTION_EMBED).

    Args:
        job: The job containing document_id, staging_key, filename, mime_type,
            and knowledge_base_id in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        FileStagingError: If staged file cannot be retrieved.
        Exception: If text extraction fails (triggers retry).

    """
    from .core.config import get_config_manager
    from .core.database import get_async_session_local
    from .core.queue_backend import get_queue_backend
    from .core.workload_routing import WorkloadType, enqueue_job
    from .models.document import Document, DocumentStatus
    from .services.file_staging_service import FileStagingError, FileStagingService

    # Validate required payload fields
    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("INGESTION_OCR job missing document_id in payload")

    knowledge_base_id = job.payload.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("INGESTION_OCR job missing knowledge_base_id in payload")

    staging_key = job.payload.get("staging_key")
    if not staging_key:
        raise ValueError("INGESTION_OCR job missing staging_key in payload")

    filename = job.payload.get("filename")
    if not filename:
        raise ValueError("INGESTION_OCR job missing filename in payload")

    mime_type = job.payload.get("mime_type")
    if not mime_type:
        raise ValueError("INGESTION_OCR job missing mime_type in payload")

    ocr_mode = job.payload.get("ocr_mode")
    user_id = job.payload.get("user_id")

    logger.info(
        "Processing OCR job",
        extra={
            "job_id": job.id,
            "document_id": document_id,
            "knowledge_base_id": knowledge_base_id,
            "file_name": filename,
        },
    )

    session_local = get_async_session_local()
    staging_service = FileStagingService()

    async with session_local() as session:
        # Get document and update status to EXTRACTING
        from sqlalchemy import select

        from .models.knowledge_base import KnowledgeBase

        result = await session.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()

        if not document:
            # Document was deleted after job was enqueued — permanent failure, no retry.
            logger.error(
                "Document not found for OCR job, failing permanently",
                extra={"job_id": job.id, "document_id": document_id},
            )
            return

        # Check KB existence before retrieving staged bytes — frees staging memory immediately
        # if the KB was deleted while this job was queued.
        kb = await session.get(KnowledgeBase, knowledge_base_id)
        if kb is None:
            logger.info(
                "Knowledge base deleted, discarding OCR job without retry",
                extra={"job_id": job.id, "document_id": document_id, "knowledge_base_id": knowledge_base_id},
            )
            try:
                await staging_service.delete_staged_file(staging_key)
            except Exception:
                pass  # Non-fatal; orphaned files are cleaned up by IngestionStagingMaintenanceSource
            return

        document.update_status(DocumentStatus.EXTRACTING)
        await session.commit()

        try:
            # Resolve the staged file to a local path (the staging dir is a
            # ReadWriteMany mount in production, so "local" is always valid).
            # The file is NOT deleted here; delete_staged_file fires on success
            # after the embed job enqueues, and on failure we leave it in place
            # so the retry can re-open it.
            staged_path = await staging_service.retrieve_to_path(staging_key)

            logger.info(
                "Resolved staged file path",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "staged_path": str(staged_path),
                },
            )

            from .core.ocr_service import extract_text_with_ocr_fallback

            extraction_result = await extract_text_with_ocr_fallback(
                mime_type=mime_type,
                config_manager=get_config_manager(),
                file_path=str(staged_path),
                filename=filename,
                ocr_mode=ocr_mode or "auto",
                user_id=user_id,
            )
            extracted_text = extraction_result.get("text", "")
            extraction_metadata = extraction_result.get("metadata", {})

            logger.info(
                "Text extraction complete",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "text_length": len(extracted_text),
                    "extraction_method": extraction_metadata.get("method"),
                    "extraction_engine": extraction_metadata.get("engine"),
                },
            )

            # Update document with extracted content and metadata
            document.content = extracted_text
            document.extraction_method = extraction_metadata.get("method")
            document.extraction_engine = extraction_metadata.get("engine")
            document.extraction_confidence = extraction_metadata.get("confidence")
            document.extraction_duration = extraction_metadata.get("duration")
            document.extraction_metadata = extraction_metadata.get("details")
            await session.commit()

            # Enqueue INGESTION_EMBED job for next stage.
            # Commit EMBEDDING status only after enqueue succeeds to avoid a window
            # where the document is EMBEDDING with no job in the queue.
            queue = await get_queue_backend()
            await enqueue_job(
                queue,
                WorkloadType.INGESTION_EMBED,
                payload={
                    "document_id": document_id,
                    "knowledge_base_id": knowledge_base_id,
                    "user_id": user_id,
                    "action": "embed_document",
                },
                max_attempts=3,
                visibility_timeout=300,
            )

            document.update_status(DocumentStatus.EMBEDDING)
            await session.commit()

            # Clean up staged file now that extraction succeeded.
            # Failure here is non-fatal — orphaned files are cleaned by
            # IngestionStagingMaintenanceSource.
            try:
                await staging_service.delete_staged_file(staging_key)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to delete staged file after successful OCR (non-fatal, orphaned files cleaned by maintenance job)",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "staging_key": staging_key,
                        "error": str(cleanup_err),
                    },
                )

            logger.info(
                "OCR job completed, enqueued embedding job",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                },
            )

        except FileStagingError as e:
            # Permanent error - file not found in staging
            logger.error(
                "File staging error",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "error": str(e),
                },
            )
            document.mark_error(f"{_ERR_FILE_STAGING} {e}")
            await session.commit()
            raise

        except Exception as e:
            # Check if we've exhausted retries
            if job.attempts >= job.max_attempts:
                logger.error(
                    "OCR job failed after max attempts",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": str(e),
                    },
                )
                document.mark_error(f"Text extraction failed after {job.attempts} attempts: {e}")
                await session.commit()
            raise


async def _handle_embed_job(job) -> None:
    """Handle an INGESTION_EMBED workload job.

    Dispatches based on the ``action`` field in the job payload:
    - ``embed_document`` (default): Chunk text and embed content vectors.
    - ``embed_profile_artifacts``: Embed profile artifacts (synopsis, chunk
      summaries, synthesized queries) after profiling completes.

    Args:
        job: The job containing document_id and knowledge_base_id in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        Exception: If embedding generation fails (triggers retry).

    """
    action = job.payload.get("action", "embed_document")

    if action == "embed_profile_artifacts":
        await _handle_profile_artifact_embed_job(job)
        return

    if action == "embed_document":
        await _handle_content_embed_job(job)
        return

    raise ValueError(f"Unknown INGESTION_EMBED action: {action!r}")


async def _handle_content_embed_job(job) -> None:
    """Handle chunk content embedding (the original embed job logic)."""
    from sqlalchemy import select

    from .core.config import get_settings_instance
    from .core.database import get_async_session_local
    from .models.document import Document, DocumentStatus
    from .services.document_service import DocumentService

    # Validate required payload fields
    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("INGESTION_EMBED job missing document_id in payload")

    knowledge_base_id = job.payload.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("INGESTION_EMBED job missing knowledge_base_id in payload")

    user_id = job.payload.get("user_id")

    logger.info(
        "Processing embedding job",
        extra={
            "job_id": job.id,
            "document_id": document_id,
            "knowledge_base_id": knowledge_base_id,
        },
    )

    session_local = get_async_session_local()

    async with session_local() as session:
        # Get document from database
        result = await session.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()

        if not document:
            # Document was deleted after job was enqueued — permanent failure, no retry.
            logger.error(
                "Document not found for embed job, failing permanently",
                extra={"job_id": job.id, "document_id": document_id},
            )
            return

        # Check KB existence before doing any embedding work — discard without retry if gone.
        from .models.knowledge_base import KnowledgeBase

        kb = await session.get(KnowledgeBase, knowledge_base_id)
        if kb is None:
            logger.info(
                "Knowledge base deleted, discarding embed job without retry",
                extra={"job_id": job.id, "document_id": document_id, "knowledge_base_id": knowledge_base_id},
            )
            return

        try:
            # The KB's embedding_model defaults to SHU_EMBEDDING_MODEL (the local
            # model name) at creation time. When the active service is an external
            # provider, the default is wrong. Correct it on the first document so
            # stale-KB detection at startup compares against the model that actually
            # produced the vectors. Only runs once — subsequent documents skip this.
            if kb.total_chunks == 0:
                from .core.embedding_service import get_embedding_service

                embedding_svc = await get_embedding_service()
                if kb.embedding_model != embedding_svc.model_name:
                    kb.embedding_model = embedding_svc.model_name
                    # This correction IS the moment the KB becomes consistent with the
                    # active embedding service. If a prior detect_stale_kbs run had flipped
                    # the KB to 'stale' based on the old default model name, clear that now —
                    # the chunks we're about to write will be produced by the current service.
                    if kb.embedding_status == "stale":
                        kb.embedding_status = "current"

            # Set EMBEDDING status before processing so a crash mid-embed leaves
            # the document in a diagnosable state rather than the previous status.
            document.update_status(DocumentStatus.EMBEDDING)
            await session.commit()

            # Process chunks and embeddings using DocumentService
            doc_service = DocumentService(session)
            try:
                word_count, char_count, chunk_count = await doc_service.process_and_update_chunks(
                    knowledge_base_id,
                    document,
                    document.title,  # type: ignore[arg-type]  # SQLAlchemy Column resolves at runtime
                    document.content,  # type: ignore[arg-type]
                    user_id=user_id,
                )
            except ValueError as kb_err:
                # KB was deleted between OCR and embed stages — permanent failure, no retry.
                logger.error(
                    "Knowledge base not found for embed job, failing permanently",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "knowledge_base_id": knowledge_base_id,
                        "error": str(kb_err),
                    },
                )
                document.mark_error(f"Knowledge base not found: {kb_err}")
                await session.commit()
                return

            logger.info(
                "Embedding generation complete",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "word_count": word_count,
                    "character_count": char_count,
                    "chunk_count": chunk_count,
                },
            )

            settings = get_settings_instance()
            await _finalize_embed_job(
                job, session, document, document_id, settings.enable_document_profiling, user_id=user_id
            )

        except Exception as e:
            # Check if we've exhausted retries
            if job.attempts >= job.max_attempts:
                logger.error(
                    "Embedding job failed after max attempts",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": str(e),
                    },
                )
                document.mark_error(f"Embedding generation failed after {job.attempts} attempts: {e}")
                await session.commit()
            raise


async def _finalize_embed_job(
    job, session, document, document_id: str, profiling_enabled: bool, *, user_id: str | None = None
) -> None:
    """Set CONTENT_PROCESSED, collect KB stats, then optionally enqueue profiling."""
    from .core.queue_backend import get_queue_backend
    from .core.workload_routing import WorkloadType, enqueue_job
    from .models.document import DocumentStatus
    from .services.knowledge_base_service import KnowledgeBaseService

    # Mark content processed — chunks + content vectors exist, document is searchable.
    # This is the first reliable point for KB stats (chunks are committed).
    document.update_status(DocumentStatus.CONTENT_PROCESSED)
    await session.commit()

    # Collect accurate KB stats now that chunks exist
    kb_id = document.knowledge_base_id
    if kb_id:
        try:
            kb_service = KnowledgeBaseService(session)
            await kb_service.recalculate_kb_stats(kb_id)
        except Exception:
            logger.warning(
                "Failed to recalculate KB stats after embedding",
                extra={"job_id": job.id, "document_id": document_id, "kb_id": kb_id},
                exc_info=True,
            )

    if profiling_enabled:
        queue = await get_queue_backend()
        await enqueue_job(
            queue,
            WorkloadType.PROFILING,
            payload={"document_id": document_id, "user_id": user_id, "action": "profile_document"},
            max_attempts=5,
            visibility_timeout=600,
        )
        # Commit PROFILING status only after enqueue succeeds to avoid a window
        # where the document is PROFILING with no job in the queue.
        document.update_status(DocumentStatus.PROFILING)
        await session.commit()
        logger.info(
            "Embedding job completed, enqueued profiling job",
            extra={"job_id": job.id, "document_id": document_id},
        )
    else:
        logger.info(
            "Embedding job completed, document ready (profiling disabled)",
            extra={"job_id": job.id, "document_id": document_id},
        )


async def _handle_profile_artifact_embed_job(job) -> None:
    """Handle profile artifact embedding after profiling completes.

    Embeds synopsis, chunk summaries, and synthesized queries that were
    persisted as text during profiling. On success, transitions the document
    to PROFILE_PROCESSED. On failure after max retries, leaves the document
    at RAG_PROCESSED (still searchable via chunk content vectors + metadata).
    """
    from sqlalchemy import select

    from .core.database import get_async_session_local
    from .models.document import Document, DocumentStatus
    from .services.profiling_orchestrator import embed_profile_artifacts

    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("embed_profile_artifacts job missing document_id in payload")

    user_id = job.payload.get("user_id")

    logger.info(
        "Processing profile artifact embedding job",
        extra={"job_id": job.id, "document_id": document_id},
    )

    session_local = get_async_session_local()

    async with session_local() as session:
        stmt = select(Document).where(Document.id == document_id)
        result = await session.execute(stmt)
        document = result.scalar_one_or_none()

        if not document:
            logger.error(
                "Document not found for artifact embed job, failing permanently",
                extra={"job_id": job.id, "document_id": document_id},
            )
            return

        try:
            document.update_status(DocumentStatus.ARTIFACT_EMBEDDING)
            await session.commit()

            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(
                session, document, user_id=user_id
            )

            document.update_status(DocumentStatus.PROFILE_PROCESSED)
            await session.commit()

            logger.info(
                "Profile artifact embedding completed",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "synopsis_embedded": synopsis_embedded,
                    "chunk_summaries_embedded": chunk_summaries_embedded,
                    "queries_embedded": queries_embedded,
                    "status": DocumentStatus.PROFILE_PROCESSED.value,
                },
            )

        except Exception as e:
            if job.attempts >= job.max_attempts:
                # Leave at RAG_PROCESSED — document is still searchable via
                # chunk content vectors and profiling metadata, just without
                # multi-surface artifact vector search.
                logger.error(
                    "Profile artifact embedding failed after max attempts, leaving as RAG_PROCESSED",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": str(e),
                    },
                )
                document.update_status(DocumentStatus.RAG_PROCESSED)
                await session.commit()
                return
            raise


async def _handle_plugin_execution_job(job) -> None:  # noqa: PLR0915
    """Handle an INGESTION plugin feed execution job.

    Loads the PluginExecution record, verifies it is still PENDING (race guard),
    resolves the plugin, builds host capabilities, runs the plugin via EXECUTOR,
    and updates execution status.

    Args:
        job: The job containing execution_id, schedule_id, plugin_name, user_id,
            agent_key, and params in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        Exception: If plugin execution fails (triggers retry).

    """
    from fastapi import HTTPException
    from sqlalchemy import select

    from .core.config import get_settings_instance
    from .core.database import get_async_session_local
    from .models.plugin_execution import PluginExecution, PluginExecutionStatus

    execution_id = job.payload.get("execution_id")
    if not execution_id:
        raise ValueError("Plugin execution job missing execution_id in payload")

    plugin_name = job.payload.get("plugin_name")
    if not plugin_name:
        raise ValueError("Plugin execution job missing plugin_name in payload")

    logger.info(
        "Processing plugin execution job",
        extra={
            "job_id": job.id,
            "execution_id": execution_id,
            "plugin_name": plugin_name,
        },
    )

    settings = get_settings_instance()
    session_local = get_async_session_local()

    async with session_local() as session:
        # Load execution record and check it's still PENDING (race guard)
        result = await session.execute(
            select(PluginExecution).where(PluginExecution.id == execution_id).with_for_update(skip_locked=True)
        )
        rec = result.scalar_one_or_none()

        if not rec:
            logger.warning(
                "Plugin execution record not found, skipping",
                extra={"job_id": job.id, "execution_id": execution_id},
            )
            return

        if rec.status != PluginExecutionStatus.PENDING:  # type: ignore[union-attr]
            logger.info(
                "Plugin execution no longer PENDING, skipping",
                extra={
                    "job_id": job.id,
                    "execution_id": execution_id,
                    "current_status": rec.status,
                },
            )
            return

        # Mark as RUNNING
        from datetime import UTC, datetime

        rec.status = PluginExecutionStatus.RUNNING  # type: ignore[assignment]  # SQLAlchemy Column
        rec.started_at = datetime.now(UTC)  # type: ignore[assignment]
        await session.commit()

        import asyncio

        async def _heartbeat_loop(execution_id: str, interval: int = 60) -> None:
            """Touch the PluginExecution row every `interval` seconds so updated_at
            advances while the plugin is running. cleanup_stale_executions uses
            updated_at as the stale cutoff, so a healthy worker is never marked stale.
            Also extends the queue job's visibility timeout so a competing consumer
            cannot re-deliver the job while this worker is still alive.
            Uses a separate session to commit independently of the main execution session.
            """
            heartbeat_session_local = get_async_session_local()
            try:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        async with heartbeat_session_local() as hb_session:
                            hb_result = await hb_session.execute(
                                select(PluginExecution).where(PluginExecution.id == execution_id)
                            )
                            hb_rec = hb_result.scalar_one_or_none()
                            if hb_rec and hb_rec.status == PluginExecutionStatus.RUNNING:  # type: ignore[union-attr]
                                # Touch updated_at via onupdate trigger
                                hb_rec.updated_at = datetime.now(UTC)
                                await hb_session.commit()
                                logger.debug(
                                    "Plugin execution heartbeat",
                                    extra={"execution_id": execution_id},
                                )
                    except asyncio.CancelledError:
                        raise
                    except Exception as hb_err:
                        # Non-fatal: log and keep looping
                        logger.warning(
                            "Plugin execution heartbeat failed (non-fatal)",
                            extra={"execution_id": execution_id, "error": str(hb_err)},
                        )

                    # Extend queue visibility so a competing consumer cannot
                    # re-deliver this job while we are still running.
                    try:
                        from .core.queue_backend import get_queue_backend

                        queue = await get_queue_backend()
                        extended = await queue.extend_visibility(job, additional_seconds=interval * 2)
                        if not extended:
                            logger.warning(
                                "extend_visibility returned False — job may have been re-delivered",
                                extra={"execution_id": execution_id, "job_id": job.id},
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as ev_err:
                        # Non-fatal: heartbeat DB touch already succeeded
                        logger.warning(
                            "extend_visibility failed (non-fatal)",
                            extra={"execution_id": execution_id, "job_id": job.id, "error": str(ev_err)},
                        )
            except asyncio.CancelledError:
                pass

        heartbeat_task = asyncio.create_task(_heartbeat_loop(execution_id))

        try:
            from .services.plugin_execution_runner import execute_plugin_record

            await execute_plugin_record(session, rec, settings)
            await session.commit()

            logger.info(
                "Plugin execution job completed",
                extra={
                    "job_id": job.id,
                    "execution_id": execution_id,
                    "plugin_name": plugin_name,
                    "status": rec.status,
                },
            )

        except HTTPException as he:
            code = he.status_code
            detail = he.detail if isinstance(he.detail, dict) else {}
            err = str(detail.get("error") or "")
            if code == 429 and err in (
                "provider_rate_limited",
                "provider_concurrency_limited",
                "rate_limited",
            ):
                # Rate limited: set back to PENDING and raise so the queue backend
                # re-enqueues the job with its retry/visibility timeout mechanism.
                logger.info(
                    "Deferred plugin execution due to 429 (%s) | plugin=%s exec_id=%s",
                    err,
                    rec.plugin_name,
                    rec.id,
                )
                rec.status = PluginExecutionStatus.PENDING  # type: ignore[assignment]
                rec.started_at = None  # type: ignore[assignment]
                rec.error = f"deferred:{err}"  # type: ignore[assignment]
                await session.commit()
                raise

            # Other HTTP errors: mark failed
            logger.exception(
                "Plugin execution HTTPException | plugin=%s exec_id=%s",
                rec.plugin_name,
                rec.id,
            )
            rec.status = PluginExecutionStatus.FAILED  # type: ignore[assignment]
            rec.error = str(he.detail)  # type: ignore[assignment]
            rec.completed_at = datetime.now(UTC)  # type: ignore[assignment]
            await session.commit()

        except Exception as e:
            logger.exception(
                "Plugin execution failed | plugin=%s exec_id=%s",
                rec.plugin_name,
                rec.id,
            )
            # If retries remain, reset to PENDING so the requeued job can
            # pick up the record again (the race guard checks for PENDING).
            # Only mark FAILED on the final attempt.
            if job.attempts < job.max_attempts:
                rec.status = PluginExecutionStatus.PENDING  # type: ignore[assignment]
                rec.started_at = None  # type: ignore[assignment]
                rec.error = str(e)  # type: ignore[assignment]
            else:
                rec.status = PluginExecutionStatus.FAILED  # type: ignore[assignment]
                rec.error = str(e)  # type: ignore[assignment]
                rec.completed_at = datetime.now(UTC)  # type: ignore[assignment]
            await session.commit()
            raise

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


async def _fail_queued_run(session, run_id: str | None, error: str) -> None:
    """Mark a pre-created queued ExperienceRun as failed when the worker skips execution."""
    if not run_id:
        return
    try:
        from datetime import UTC, datetime

        from sqlalchemy import select

        from .models.experience import ExperienceRun

        result = await session.execute(select(ExperienceRun).where(ExperienceRun.id == run_id))
        run = result.scalar_one_or_none()
        if run and run.status == "queued":
            run.status = "failed"
            run.error_message = error
            run.finished_at = datetime.now(UTC)
            await session.commit()
    except Exception:
        pass


async def _handle_experience_execution_job(job) -> None:
    """Handle an LLM_WORKFLOW experience execution job.

    Delegates to ExperienceService.execute() which handles authorization,
    experience loading, shared-experience identity resolution, and execution.

    Args:
        job: The job containing experience_id, user_id, and input_params
            in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        Exception: If execution fails (triggers retry).

    """
    experience_id = job.payload.get("experience_id")
    if not experience_id:
        raise ValueError("Experience execution job missing experience_id in payload")

    user_id = job.payload.get("user_id")
    input_params = job.payload.get("input_params", {})
    run_id = job.payload.get("run_id")

    logger.info(
        "Processing experience execution job",
        extra={
            "job_id": job.id,
            "experience_id": experience_id,
            "user_id": user_id,
            "run_id": run_id,
        },
    )

    session_local = get_async_session_local()

    async with session_local() as session:
        user = None
        if user_id:
            user_result = await session.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            if not user:
                logger.warning("User not found, skipping", extra={"job_id": job.id, "user_id": user_id})
                await _fail_queued_run(session, run_id, "user_not_found")
                return
            if not user.is_active:  # type: ignore[truthy-bool]
                logger.debug("User inactive, skipping", extra={"job_id": job.id, "user_id": user_id})
                await _fail_queued_run(session, run_id, "user_inactive")
                return

        try:
            service = ExperienceService(session)
            run = await service.execute(
                experience_id=experience_id,
                current_user=user,
                input_params=input_params,
                run_id=run_id,
            )

            logger.info(
                "Experience execution completed",
                extra={
                    "job_id": job.id,
                    "experience_id": experience_id,
                    "user_id": user_id,
                    "run_id": run.id if run else None,
                    "status": run.status if run else "unknown",
                },
            )

        except ShuException as e:
            logger.warning(
                "Experience execution denied | experience=%s user=%s error=%s",
                experience_id,
                user_id,
                str(e),
            )
            await _fail_queued_run(session, run_id, e.error_code)
        except Exception:
            logger.exception(
                "Experience execution failed | experience=%s user=%s",
                experience_id,
                user_id,
            )
            if job.attempts >= job.max_attempts:
                await _fail_queued_run(session, run_id, "execution_error")
            raise


async def _handle_profiling_job(job) -> None:
    """Handle a PROFILING workload job.

    Runs the profiling orchestrator for the specified document. On success,
    sets the document's pipeline status to RAG_PROCESSED and enqueues an
    INGESTION_EMBED job for artifact embedding (SHU-637).

    Args:
        job: The job containing document_id in payload.

    Raises:
        ValueError: If document_id is missing from payload.
        Exception: If profiling fails (triggers retry).

    """
    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("PROFILING job missing document_id in payload")

    user_id = job.payload.get("user_id")

    logger.info("Processing profiling job", extra={"job_id": job.id, "document_id": document_id})

    from sqlalchemy import select

    from .core.config import get_config_manager, get_settings_instance
    from .core.database import get_async_session_local
    from .models.document import Document, DocumentStatus
    from .services.profiling_orchestrator import ProfilingOrchestrator
    from .services.side_call_service import SideCallService

    settings = get_settings_instance()
    session_local = get_async_session_local()

    async with session_local() as session:
        config_manager = get_config_manager()
        side_call_service = SideCallService(session, config_manager)
        orchestrator = ProfilingOrchestrator(session, settings, side_call_service)

        result = await orchestrator.run_for_document(document_id, user_id=user_id)

        if result.skipped:
            # skipped==True means the job had nothing to do (e.g. document not found). It is appropriate to bail without retrying.
            logger.info(
                "Profiling job skipped - precondition not met",
                extra={"job_id": job.id, "document_id": document_id, "reason": result.error},
            )
            return

        if result.success:
            # Set document pipeline status to RAG_PROCESSED — profiling text artifacts
            # are persisted but artifact embeddings haven't been generated yet.
            # Enqueue an INGESTION_EMBED job for artifact embedding.
            stmt = select(Document).where(Document.id == document_id)
            doc_result = await session.execute(stmt)
            document = doc_result.scalar_one_or_none()

            if document:
                from .core.queue_backend import get_queue_backend
                from .core.workload_routing import WorkloadType, enqueue_job

                queue = await get_queue_backend()
                await enqueue_job(
                    queue,
                    WorkloadType.INGESTION_EMBED,
                    payload={
                        "document_id": document_id,
                        "knowledge_base_id": document.knowledge_base_id,
                        "user_id": user_id,
                        "action": "embed_profile_artifacts",
                    },
                    max_attempts=3,
                    visibility_timeout=300,
                )
                # Commit RAG_PROCESSED only after enqueue succeeds
                document.update_status(DocumentStatus.RAG_PROCESSED)
                await session.commit()

            logger.info(
                "Profiling job completed, enqueued artifact embedding",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "profiling_mode": result.profiling_mode.value if result.profiling_mode else None,
                    "tokens_used": result.tokens_used,
                    "duration_ms": result.duration_ms,
                    "status": DocumentStatus.RAG_PROCESSED.value,
                },
            )
        else:
            # Only mark ERROR when retries are exhausted (mirrors OCR/EMBED pattern)
            error_msg = f"Profiling failed: {result.error}"
            if job.attempts >= job.max_attempts:
                stmt = select(Document).where(Document.id == document_id)
                doc_result = await session.execute(stmt)
                document = doc_result.scalar_one_or_none()
                if document:
                    document.mark_error(error_msg)
                    await session.commit()

                logger.error(
                    "Profiling job failed after max attempts",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": result.error,
                    },
                )
            else:
                logger.warning(
                    "Profiling job failed, will retry",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": result.error,
                    },
                )
            raise Exception(f"Profiling failed for document {document_id}: {result.error}")


async def _handle_re_embedding_job(job) -> None:
    """Route RE_EMBEDDING jobs to the handler module."""
    from .re_embedding_handler import handle_re_embedding_job

    await handle_re_embedding_job(job)


async def process_job(job):  # noqa: PLR0912 — dispatch table by workload type; flatter than a handler registry here
    """Process a job based on its workload type and payload.

    Routes jobs to appropriate handlers based on the queue name (workload type).

    Args:
        job: The job to process.

    Raises:
        ValueError: If job has unknown workload type or invalid payload.
        Exception: For transient errors that should trigger retry.

    """
    import time as _time

    from .core.config import get_settings_instance as _get_settings
    from .core.memory_tools import current_rss_bytes
    from .core.workload_routing import WorkloadType

    # Determine workload type from queue name
    workload_type = None
    for wt in WorkloadType:
        if job.queue_name == wt.queue_name:
            workload_type = wt
            break

    if workload_type is None:
        raise ValueError(f"Unknown queue name: {job.queue_name}")

    # Per-job RSS delta logging (SHU-731). Reads /proc/self/status VmRSS —
    # ~100 µs per sample, cheap enough to keep on by default.
    log_rss = getattr(_get_settings(), "memory_log_per_job_rss", True)
    rss_before = current_rss_bytes() if log_rss else 0
    t_start = _time.time()

    try:
        # Route to appropriate handler
        if workload_type == WorkloadType.PROFILING:
            await _handle_profiling_job(job)

        elif workload_type == WorkloadType.INGESTION_OCR:
            await _handle_ocr_job(job)

        elif workload_type == WorkloadType.INGESTION_EMBED:
            await _handle_embed_job(job)

        elif workload_type == WorkloadType.RE_EMBEDDING:
            await _handle_re_embedding_job(job)

        elif workload_type == WorkloadType.MAINTENANCE:
            # Reserved for future maintenance tasks (cache cleanup, session expiry, etc.)
            logger.warning(
                "MAINTENANCE workload handler not yet implemented",
                extra={
                    "job_id": job.id,
                    "queue": job.queue_name,
                },
            )

        elif workload_type == WorkloadType.INGESTION:
            # Route based on action in payload
            action = (job.payload or {}).get("action", "")
            if action == "plugin_feed_execution":
                await _handle_plugin_execution_job(job)
            else:
                raise ValueError(f"INGESTION job {job.id} has unknown action: {action!r}")

        elif workload_type == WorkloadType.LLM_WORKFLOW:
            # Route based on action in payload
            action = (job.payload or {}).get("action", "")
            if action == "experience_execution":
                await _handle_experience_execution_job(job)
            else:
                raise ValueError(f"LLM_WORKFLOW job {job.id} has unknown action: {action!r}")

        else:
            raise ValueError(f"Unsupported workload type: {workload_type}")
    finally:
        if log_rss:
            rss_after = current_rss_bytes()
            logger.info(
                "job_memory_delta",
                extra={
                    "job_id": job.id,
                    "workload_type": workload_type.value if workload_type else "unknown",
                    "document_id": (job.payload or {}).get("document_id"),
                    "rss_before_bytes": rss_before,
                    "rss_after_bytes": rss_after,
                    "rss_delta_bytes": rss_after - rss_before,
                    "duration_ms": int((_time.time() - t_start) * 1000),
                },
            )


async def _run_log_maintenance() -> None:
    """Periodically call ManagedFileHandler.rotate_if_needed().

    Worker processes are long-lived but don't run the unified scheduler
    (that's the API process's job). This lightweight loop ensures their
    log files still get midnight rotation and retention cleanup.
    """
    from .core.logging import get_managed_file_handler

    while True:
        try:
            handler = get_managed_file_handler()
            if handler is not None:
                handler.rotate_if_needed()
        except Exception as e:
            logger.debug("Log maintenance tick failed (non-fatal): %s", e)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break


async def run_worker(  # noqa: PLR0915 — linear startup/shutdown sequence; splitting would obscure ordering
    workload_types: set[WorkloadType],
    poll_interval: float = 1.0,
    shutdown_timeout: float = 30.0,
    concurrency: int = 1,
) -> None:
    """Run the worker loop with configurable concurrency.

    Args:
        workload_types: Set of workload types to consume.
        poll_interval: Seconds between dequeue attempts when idle.
        shutdown_timeout: Seconds to wait for current job on shutdown.
        concurrency: Number of concurrent worker tasks to run.

    """
    # Initialize database connection
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database: %s", e, exc_info=True)
        sys.exit(1)

    # Get queue backend (shared by all workers)
    try:
        backend = await get_queue_backend()
        logger.info("Queue backend initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize queue backend: %s", e, exc_info=True)
        sys.exit(1)

    # Start a lightweight log-maintenance loop so that worker processes
    # (which are long-lived but don't run the full unified scheduler)
    # still get midnight rotation and retention cleanup on their log files.
    log_maintenance_task = asyncio.create_task(_run_log_maintenance(), name="worker:log-maintenance")

    # Periodic gc.collect() + malloc_trim(0) (SHU-731). Workers do the bulk
    # of profiling allocation but never enter the FastAPI lifespan, so we
    # spawn a parallel trim loop here. Default 60s in all configmaps; set
    # SHU_MEMORY_TRIM_INTERVAL_SECONDS=0 to disable. malloc_trim is a no-op
    # under jemalloc but gc.collect() still clears Python gen-2 cycles, so
    # the loop is useful regardless of allocator.
    trim_task: asyncio.Task | None = None
    try:
        from .core.config import get_settings_instance as _get_settings
        from .core.memory_tools import periodic_trim_loop as _trim_loop

        trim_interval = getattr(_get_settings(), "memory_trim_interval_seconds", 0.0)
        if trim_interval and trim_interval > 0:
            trim_task = asyncio.create_task(_trim_loop(trim_interval), name="worker:memory-trim")
            logger.info("Memory trim task started (interval=%ss)", trim_interval)
    except Exception as e:
        logger.warning("Failed to start memory trim task: %s", e)

    # Create worker configuration
    config = WorkerConfig(
        workload_types=workload_types,
        poll_interval=poll_interval,
        shutdown_timeout=shutdown_timeout,
    )

    # Create N concurrent workers
    concurrency = max(1, concurrency)
    workers = []
    for i in range(concurrency):
        worker_id = f"{i + 1}/{concurrency}"
        worker = Worker(backend, config, job_handler=process_job, worker_id=worker_id)
        workers.append(worker)

    logger.info(
        "Starting dedicated workers",
        extra={
            "concurrency": concurrency,
            "workload_types": [wt.value for wt in workload_types],
            "poll_interval": poll_interval,
            "shutdown_timeout": shutdown_timeout,
        },
    )

    try:
        # Run all workers concurrently, logging failures immediately via done callbacks
        tasks = []
        for i, w in enumerate(workers):
            task = asyncio.create_task(w.run())
            wid = w.worker_id if hasattr(w, "worker_id") else f"{i + 1}/{len(workers)}"

            def _on_done(t: asyncio.Task, worker_id: str = wid) -> None:
                if t.cancelled():
                    logger.warning("Worker %s was cancelled", worker_id)
                elif exc := t.exception():
                    logger.error("Worker %s failed with error: %s", worker_id, exc, exc_info=exc)

            task.add_done_callback(_on_done)
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        logger.info("Workers interrupted by user")
    except Exception as e:
        logger.error("Worker orchestration error: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        if not log_maintenance_task.done():
            log_maintenance_task.cancel()
        if trim_task is not None and not trim_task.done():
            trim_task.cancel()
        logger.info("Workers shutdown complete")


def main() -> None:
    """Start worker process."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Shu dedicated worker process",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run worker consuming all workload types
  python -m shu.worker

  # Run worker consuming specific workload types
  python -m shu.worker --workload-types=INGESTION,PROFILING

  # Run worker with custom poll interval
  python -m shu.worker --workload-types=LLM_WORKFLOW --poll-interval=0.5

Valid workload types:
  INGESTION       - Plugin feed ingestion (Gmail, Drive, Outlook, etc.)
  INGESTION_OCR   - OCR/text extraction stage of document pipeline
  INGESTION_EMBED - Embedding stage of document pipeline
  LLM_WORKFLOW    - Scheduled experience execution and LLM workflows
  MAINTENANCE     - Cleanup and scheduled maintenance tasks
  PROFILING       - Document profiling with LLM calls
        """,
    )

    parser.add_argument(
        "--workload-types",
        type=str,
        default=None,
        help="Comma-separated list of workload types to consume (e.g., INGESTION,PROFILING). "
        "If not specified, consumes all workload types.",
    )

    # Get settings first so we can use them as defaults
    settings = get_settings_instance()

    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.worker_poll_interval,
        help=f"Seconds between dequeue attempts when idle (default: {settings.worker_poll_interval})",
    )

    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=settings.worker_shutdown_timeout,
        help=f"Seconds to wait for current job on shutdown (default: {settings.worker_shutdown_timeout})",
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.worker_concurrency,
        help=f"Number of concurrent worker tasks (default: {settings.worker_concurrency})",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    # Cap the MuPDF process-global store before any fitz.open in the OCR path
    # (SHU-710). The API lifespan configures this independently for that process;
    # dedicated worker processes handle the vast majority of ingestion OCR and
    # must configure their own cap since they never run the FastAPI lifespan.
    from .processors.text_extractor import configure_mupdf_store

    configure_mupdf_store()

    logger.info(
        "Worker entrypoint starting",
        extra={
            "version": settings.version,
            "environment": settings.environment,
            "concurrency": args.concurrency,
        },
    )

    # Parse workload types
    try:
        if args.workload_types:
            workload_types = parse_workload_types(args.workload_types)
        else:
            # Default to all workload types
            workload_types = set(WorkloadType)
            logger.info("No workload types specified, consuming all types")
    except ValueError as e:
        logger.error("Invalid workload types: %s", e)
        sys.exit(1)

    # Run worker
    try:
        asyncio.run(
            run_worker(
                workload_types=workload_types,
                poll_interval=args.poll_interval,
                shutdown_timeout=args.shutdown_timeout,
                concurrency=args.concurrency,
            )
        )
    except Exception as e:
        logger.error("Worker failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
