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

from .core.config import get_settings_instance
from .core.database import init_db
from .core.logging import get_logger, setup_logging
from .core.queue_backend import get_queue_backend
from .core.worker import Worker, WorkerConfig
from .core.workload_routing import WorkloadType

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
    from .core.database import get_async_session_local
    from .core.queue_backend import get_queue_backend
    from .core.workload_routing import WorkloadType, enqueue_job
    from .models.document import Document, DocumentStatus
    from .processors.text_extractor import TextExtractor
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
                pass  # Non-fatal; file will TTL-expire
            return

        document.update_status(DocumentStatus.EXTRACTING)
        await session.commit()

        try:
            # Retrieve file bytes from staging (don't delete yet - need retry safety)
            file_bytes = await staging_service.retrieve_file(staging_key, delete_after_retrieve=False)

            logger.info(
                "Retrieved staged file",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "file_size": len(file_bytes),
                },
            )

            # Extract text using TextExtractor
            extractor = TextExtractor()

            # Determine if OCR should be used based on ocr_mode.
            # "text_only" → no OCR; all other modes (including "fallback") let the
            # extractor decide per file type.  "fallback" must also be forwarded via
            # progress_context so the PDF path can try fast extraction first.
            use_ocr = ocr_mode != "text_only" if ocr_mode else True
            progress_ctx = {"ocr_mode": ocr_mode} if ocr_mode else None

            extraction_result = await extractor.extract_text(
                file_path=filename,
                file_content=file_bytes,
                use_ocr=use_ocr,
                progress_context=progress_ctx,
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

            # Update status to EMBEDDING
            document.update_status(DocumentStatus.EMBEDDING)
            await session.commit()

            # Enqueue INGESTION_EMBED job for next stage
            queue = await get_queue_backend()
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

            # Clean up staged file now that extraction succeeded.
            # Failure here is non-fatal — the file will TTL-expire on its own.
            try:
                await staging_service.delete_staged_file(staging_key)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to delete staged file after successful OCR (non-fatal, will TTL-expire)",
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
            document.mark_error(f"File staging failed: {e}")
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

    Retrieves the document from the database, generates chunks and embeddings,
    and either enqueues a PROFILING job (if enabled) or sets status to PROCESSED.

    Args:
        job: The job containing document_id and knowledge_base_id in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        Exception: If embedding generation fails (triggers retry).

    """
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
            await _finalize_embed_job(job, session, document, document_id, settings.enable_document_profiling)

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


async def _finalize_embed_job(job, session, document, document_id: str, profiling_enabled: bool) -> None:
    """Enqueue a profiling job or mark the document PROCESSED after embedding."""
    from .core.queue_backend import get_queue_backend
    from .core.workload_routing import WorkloadType, enqueue_job
    from .models.document import DocumentStatus

    if profiling_enabled:
        queue = await get_queue_backend()
        await enqueue_job(
            queue,
            WorkloadType.PROFILING,
            payload={"document_id": document_id, "action": "profile_document"},
            max_attempts=5,
            visibility_timeout=600,
        )
        document.update_status(DocumentStatus.PROFILING)
        await session.commit()
        logger.info(
            "Embedding job completed, enqueued profiling job",
            extra={"job_id": job.id, "document_id": document_id},
        )
    else:
        document.update_status(DocumentStatus.PROCESSED)
        await session.commit()
        logger.info(
            "Embedding job completed, document ready (profiling disabled)",
            extra={"job_id": job.id, "document_id": document_id},
        )


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

    Loads the Experience and User, instantiates ExperienceExecutor, and
    runs the experience in non-streaming mode. Creates an ExperienceRun
    record via the executor.

    Args:
        job: The job containing experience_id, user_id, and input_params
            in payload.

    Raises:
        ValueError: If required fields are missing from payload.
        Exception: If execution fails (triggers retry).

    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from .auth.models import User
    from .core.config import get_config_manager
    from .core.database import get_async_session_local
    from .models.experience import Experience
    from .services.experience_executor import ExperienceExecutor

    experience_id = job.payload.get("experience_id")
    if not experience_id:
        raise ValueError("Experience execution job missing experience_id in payload")

    user_id = job.payload.get("user_id")
    if not user_id:
        raise ValueError("Experience execution job missing user_id in payload")

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
        # Load experience with steps eagerly loaded
        exp_result = await session.execute(
            select(Experience).options(selectinload(Experience.steps)).where(Experience.id == experience_id)
        )
        experience = exp_result.scalar_one_or_none()

        if not experience:
            logger.warning(
                "Experience not found, skipping",
                extra={"job_id": job.id, "experience_id": experience_id},
            )
            await _fail_queued_run(session, run_id, "experience_not_found")
            return

        # Load user
        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()

        if not user:
            logger.warning(
                "User not found, skipping",
                extra={"job_id": job.id, "user_id": user_id},
            )
            await _fail_queued_run(session, run_id, "user_not_found")
            return

        if not user.is_active:  # type: ignore[truthy-bool]
            logger.debug(
                "User inactive, skipping experience execution",
                extra={"job_id": job.id, "user_id": user_id},
            )
            await _fail_queued_run(session, run_id, "user_inactive")
            return

        try:
            config_manager = get_config_manager()
            executor = ExperienceExecutor(session, config_manager)
            run = await executor.execute(
                experience=experience,
                user_id=user_id,
                input_params=input_params,
                current_user=user,
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

        except Exception:
            logger.exception(
                "Experience execution failed | experience=%s user=%s",
                experience_id,
                user_id,
            )
            # Let the queue retry mechanism handle transient failures
            raise


async def _handle_profiling_job(job) -> None:
    """Handle a PROFILING workload job.

    Runs the profiling orchestrator for the specified document. On success,
    sets the document's pipeline status to PROCESSED (the final state).

    Args:
        job: The job containing document_id in payload.

    Raises:
        ValueError: If document_id is missing from payload.
        Exception: If profiling fails (triggers retry).

    """
    document_id = job.payload.get("document_id")
    if not document_id:
        raise ValueError("PROFILING job missing document_id in payload")

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

        result = await orchestrator.run_for_document(document_id)

        if result.success:
            # Set document pipeline status to PROCESSED (final state)
            # The orchestrator already updated profiling_status to "complete"
            # Now we need to update the pipeline status field
            stmt = select(Document).where(Document.id == document_id)
            doc_result = await session.execute(stmt)
            document = doc_result.scalar_one_or_none()

            if document:
                document.update_status(DocumentStatus.PROCESSED)
                await session.commit()

            logger.info(
                "Profiling job completed successfully",
                extra={
                    "job_id": job.id,
                    "document_id": document_id,
                    "profiling_mode": result.profiling_mode.value if result.profiling_mode else None,
                    "tokens_used": result.tokens_used,
                    "duration_ms": result.duration_ms,
                    "status": DocumentStatus.PROCESSED.value,
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


async def process_job(job):
    """Process a job based on its workload type and payload.

    Routes jobs to appropriate handlers based on the queue name (workload type).

    Args:
        job: The job to process.

    Raises:
        ValueError: If job has unknown workload type or invalid payload.
        Exception: For transient errors that should trigger retry.

    """
    from .core.workload_routing import WorkloadType

    # Determine workload type from queue name
    workload_type = None
    for wt in WorkloadType:
        if job.queue_name == wt.queue_name:
            workload_type = wt
            break

    if workload_type is None:
        raise ValueError(f"Unknown queue name: {job.queue_name}")

    # Route to appropriate handler
    if workload_type == WorkloadType.PROFILING:
        await _handle_profiling_job(job)

    elif workload_type == WorkloadType.INGESTION_OCR:
        await _handle_ocr_job(job)

    elif workload_type == WorkloadType.INGESTION_EMBED:
        await _handle_embed_job(job)

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


async def run_worker(
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
