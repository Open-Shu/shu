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
    from .core.cache_backend import get_cache_backend
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
    cache = await get_cache_backend()
    staging_service = FileStagingService(cache)  # TTL not needed for retrieval

    async with session_local() as session:
        # Get document and update status to EXTRACTING
        from sqlalchemy import select

        result = await session.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()

        if not document:
            raise ValueError(f"Document not found: {document_id}")

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

            # Determine if OCR should be used based on ocr_mode
            use_ocr = ocr_mode != "text_only" if ocr_mode else True

            extraction_result = await extractor.extract_text(
                file_path=filename,
                file_content=file_bytes,
                use_ocr=use_ocr,
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

            # Clean up staged file now that extraction succeeded
            await staging_service.delete_staged_file(staging_key)

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
    from .core.queue_backend import get_queue_backend
    from .core.workload_routing import WorkloadType, enqueue_job
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
            raise ValueError(f"Document not found: {document_id}")

        try:
            # Process chunks and embeddings using DocumentService
            doc_service = DocumentService(session)
            word_count, char_count, chunk_count = await doc_service.process_and_update_chunks(
                knowledge_base_id,
                document,
                document.title,
                document.content,
            )

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

            # Check if profiling is enabled
            settings = get_settings_instance()
            profiling_enabled = settings.enable_document_profiling

            if profiling_enabled:
                # Update status to PROFILING and enqueue profiling job
                document.update_status(DocumentStatus.PROFILING)
                await session.commit()

                queue = await get_queue_backend()
                await enqueue_job(
                    queue,
                    WorkloadType.PROFILING,
                    payload={
                        "document_id": document_id,
                        "action": "profile_document",
                    },
                    max_attempts=5,
                    visibility_timeout=600,
                )

                logger.info(
                    "Embedding job completed, enqueued profiling job",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                    },
                )
            else:
                # Profiling disabled - set status to PROCESSED
                document.update_status(DocumentStatus.PROCESSED)
                await session.commit()

                logger.info(
                    "Embedding job completed, document ready (profiling disabled)",
                    extra={
                        "job_id": job.id,
                        "document_id": document_id,
                    },
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


# TODO: Refactor this function. It's too complex (number of branches and statements).
async def _handle_plugin_execution_job(job) -> None:  # noqa: PLR0912, PLR0915
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
    import json

    from fastapi import HTTPException
    from sqlalchemy import select

    from .core.config import get_settings_instance
    from .core.database import get_async_session_local
    from .models.plugin_execution import PluginExecution, PluginExecutionStatus
    from .models.plugin_feed import PluginFeed
    from .models.plugin_registry import PluginDefinition
    from .models.provider_identity import ProviderIdentity
    from .plugins.executor import EXECUTOR
    from .plugins.registry import REGISTRY
    from .services.plugin_identity import (
        PluginIdentityError,
        ensure_secrets_for_plugin,
        resolve_auth_requirements,
    )
    from .services.plugins_scheduler_service import ONE_SHOT_FEED_PARAMS

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

        if rec.status != PluginExecutionStatus.PENDING:
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

        rec.status = PluginExecutionStatus.RUNNING
        rec.started_at = datetime.now(UTC)
        await session.commit()

        async def _fail(error_code: str) -> None:
            rec.status = PluginExecutionStatus.FAILED
            rec.error = error_code
            rec.result = {"status": "error", "error": error_code}
            rec.completed_at = datetime.now(UTC)
            await session.commit()

        try:
            # Check if schedule is disabled
            if rec.schedule_id:
                srow = await session.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
                s = srow.scalars().first()
                if s and not s.enabled:
                    await _fail("schedule_disabled")
                    return

            # Resolve plugin
            plugin = await REGISTRY.resolve(rec.plugin_name, session)
            if not plugin:
                await _fail("plugin_not_found")
                # Auto-disable the feed to prevent repeated failures
                if rec.schedule_id:
                    try:
                        srow = await session.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
                        s = srow.scalars().first()
                        if s and s.enabled:
                            s.enabled = False
                            await session.commit()
                    except Exception:
                        pass
                return

            # Per-plugin limits
            lrow = await session.execute(select(PluginDefinition).where(PluginDefinition.name == rec.plugin_name))
            ldef = lrow.scalars().first()
            per_plugin_limits = getattr(ldef, "limits", None) or {}

            # Identity resolution
            p = rec.params or {}
            mode = str(p.get("auth_mode") or "").lower()
            user_email_val = p.get("user_email")
            if not user_email_val and mode == "domain_delegate":
                imp = p.get("impersonate_email")
                if imp:
                    user_email_val = imp

            # Build provider identities map for the owner/runner
            providers_map: dict = {}
            try:
                q_pi = select(ProviderIdentity).where(ProviderIdentity.user_id == str(rec.user_id))
                pi_res = await session.execute(q_pi)
                for pi in pi_res.scalars().all():
                    providers_map.setdefault(pi.provider_key, []).append(pi.to_dict())
            except Exception:
                logger.warning(
                    "Failed to load provider identities, proceeding with empty map | exec_id=%s user=%s",
                    rec.id,
                    rec.user_id,
                    exc_info=True,
                )
                providers_map = {}

            # Auth preflight
            try:
                from .plugins.host.auth_capability import AuthCapability

                provider, mode_eff, subject, scopes = resolve_auth_requirements(plugin, rec.params or {})
                if provider:
                    auth = AuthCapability(plugin_name=str(rec.plugin_name), user_id=str(rec.user_id))
                    mode_str = (mode_eff or "").strip().lower()
                    sc = scopes or []
                    if mode_str == "user":
                        # Subscription enforcement
                        try:
                            from .services.host_auth_service import HostAuthService

                            subs = await HostAuthService.list_subscriptions(session, str(rec.user_id), provider, None)
                            if subs:
                                subscribed_names = {s.plugin_name for s in subs}
                                if str(rec.plugin_name) not in subscribed_names:
                                    logger.warning(
                                        "subscription.enforced | user=%s provider=%s plugin=%s path=worker",
                                        str(rec.user_id),
                                        provider,
                                        str(rec.plugin_name),
                                    )
                                    await _fail("subscription_required")
                                    return
                        except Exception:
                            logger.exception(
                                "Subscription enforcement check failed, failing closed | user=%s provider=%s plugin=%s",
                                str(rec.user_id),
                                provider,
                                str(rec.plugin_name),
                            )
                            await _fail("subscription_check_error")
                            return
                        tok = await auth.provider_user_token(provider, required_scopes=sc or None)
                        if not tok:
                            await _fail("identity_required")
                            return
                    elif mode_str == "domain_delegate":
                        subj = (subject or "").strip()
                        if not subj:
                            await _fail("identity_required")
                            return
                        resp = await auth.provider_delegation_check(provider, scopes=sc, subject=subj)
                        if not (isinstance(resp, dict) and resp.get("ready") is True):
                            await _fail("identity_required")
                            return
                    elif mode_str == "service_account":
                        try:
                            _ = await auth.provider_service_account_token(provider, scopes=sc, subject=None)
                        except Exception:
                            await _fail("identity_required")
                            return
            except Exception:
                # Resolution/setup failure defaults to allow — inner checks
                # (subscription, token, delegation) handle fail-closed decisions.
                logger.warning(
                    "Auth preflight resolution failed, defaulting to allow | exec_id=%s plugin=%s",
                    rec.id,
                    rec.plugin_name,
                    exc_info=True,
                )

            # Secrets preflight
            try:
                await ensure_secrets_for_plugin(plugin, str(rec.plugin_name), str(rec.user_id), rec.params or {})
            except PluginIdentityError:
                await _fail("missing_secrets")
                return
            except Exception as e:
                logger.warning(
                    "Secrets preflight check failed unexpectedly for exec %s plugin %s: %s",
                    rec.id,
                    rec.plugin_name,
                    e,
                )

            # Inject schedule_id into params so plugins can scope cursors per-feed
            base_params = rec.params or {}
            eff_params = dict(base_params) if isinstance(base_params, dict) else {}
            if rec.schedule_id:
                eff_params["__schedule_id"] = str(rec.schedule_id)

            # Execute plugin
            exec_result = await EXECUTOR.execute(
                plugin=plugin,
                user_id=str(rec.user_id),
                user_email=user_email_val,
                agent_key=rec.agent_key,
                params=eff_params,
                limits=per_plugin_limits,
                provider_identities=providers_map,
            )

            # Normalize result to dict
            try:
                payload = exec_result.model_dump()
            except Exception:
                if isinstance(exec_result, dict):
                    payload = exec_result
                else:
                    payload = {
                        "status": getattr(exec_result, "status", None),
                        "data": getattr(exec_result, "data", None),
                        "error": getattr(exec_result, "error", None),
                    }

            # Enforce output byte cap
            try:
                payload_json = json.dumps(payload, separators=(",", ":"), default=str)
                payload_size = len(payload_json.encode("utf-8"))
            except Exception:
                payload_size = 0
            max_bytes = getattr(settings, "plugin_exec_output_max_bytes", 0) or 0
            if max_bytes > 0 and payload_size > max_bytes:
                rec.completed_at = datetime.now(UTC)
                rec.status = PluginExecutionStatus.FAILED
                rec.error = f"output exceeds max bytes ({payload_size} > {max_bytes})"
                rec.result = {"status": "error", "error": "output_too_large"}
                await session.commit()
                return

            rec.result = payload
            rec.completed_at = datetime.now(UTC)
            rec.status = (
                PluginExecutionStatus.COMPLETED if payload.get("status") == "success" else PluginExecutionStatus.FAILED
            )
            _err_val = payload.get("error") if payload.get("status") != "success" else None
            if isinstance(_err_val, (dict, list)):
                rec.error = json.dumps(_err_val, separators=(",", ":"), default=str)
            else:
                rec.error = _err_val

            # Diagnostics logging
            try:
                from .plugins.utils import log_plugin_diagnostics as _log_diags
            except Exception:
                _log_diags = None
            if _log_diags:
                _log_diags(payload, plugin_name=str(rec.plugin_name), exec_id=str(rec.id))

            # Clear one-shot params from feed after successful execution
            # and update last_run_at to reflect actual execution time
            if rec.schedule_id:
                try:
                    feed_res = await session.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
                    feed = feed_res.scalars().first()
                    if feed:
                        # Update last_run_at to actual execution completion time
                        if rec.status == PluginExecutionStatus.COMPLETED:
                            feed.last_run_at = datetime.now(UTC)
                        # Clear one-shot params on success
                        if rec.status == PluginExecutionStatus.COMPLETED and feed.params:
                            params_dict = dict(feed.params) if isinstance(feed.params, dict) else {}
                            modified = False
                            for key in ONE_SHOT_FEED_PARAMS:
                                if key in params_dict:
                                    del params_dict[key]
                                    modified = True
                            if modified:
                                feed.params = params_dict
                except Exception:
                    logger.warning(
                        "Failed to update feed after execution (one-shot params / last_run_at) | exec_id=%s schedule_id=%s",
                        rec.id,
                        rec.schedule_id,
                        exc_info=True,
                    )

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
                rec.status = PluginExecutionStatus.PENDING
                rec.started_at = None
                rec.error = f"deferred:{err}"
                await session.commit()
                raise

            # Other HTTP errors: mark failed
            logger.exception(
                "Plugin execution HTTPException | plugin=%s exec_id=%s",
                rec.plugin_name,
                rec.id,
            )
            rec.status = PluginExecutionStatus.FAILED
            rec.error = str(he.detail)
            rec.completed_at = datetime.now(UTC)
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
                rec.status = PluginExecutionStatus.PENDING
                rec.started_at = None
                rec.error = str(e)
            else:
                rec.status = PluginExecutionStatus.FAILED
                rec.error = str(e)
                rec.completed_at = datetime.now(UTC)
            await session.commit()
            raise


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

        if not user.is_active:
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
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)

    # Get queue backend (shared by all workers)
    try:
        backend = await get_queue_backend()
        logger.info("Queue backend initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize queue backend: {e}", exc_info=True)
        sys.exit(1)

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
                    logger.warning(f"Worker {worker_id} was cancelled")
                elif exc := t.exception():
                    logger.error(f"Worker {worker_id} failed with error: {exc}", exc_info=exc)

            task.add_done_callback(_on_done)
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        logger.info("Workers interrupted by user")
    except Exception as e:
        logger.error(f"Worker orchestration error: {e}", exc_info=True)
        sys.exit(1)
    finally:
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
        logger.error(f"Invalid workload types: {e}")
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
        logger.error(f"Worker failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
