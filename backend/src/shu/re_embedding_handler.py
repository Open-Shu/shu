"""Re-embedding job handlers.

Handles parallel re-embedding of knowledge base chunks across multiple workers
using a competing consumers pattern with FOR UPDATE SKIP LOCKED to prevent
duplicate work. Coordinates worker completion and enqueues finalization
(synopses, queries, indexes) when all chunk workers are done.
"""

import asyncio

from .core.logging import get_logger

logger = get_logger(__name__)


async def _re_embedding_heartbeat(job, knowledge_base_id: str, interval: int = 60) -> None:
    """Extend queue visibility every `interval` seconds so long-running
    re-embedding jobs are not redelivered to a competing consumer.
    Also touches KB updated_at via a separate session.
    """
    from datetime import UTC, datetime

    from .core.database import get_async_session_local
    from .models.knowledge_base import KnowledgeBase

    heartbeat_session_local = get_async_session_local()
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                async with heartbeat_session_local() as hb_session:
                    hb_kb = await hb_session.get(KnowledgeBase, knowledge_base_id)
                    if hb_kb and hb_kb.embedding_status == "re_embedding":
                        hb_kb.updated_at = datetime.now(UTC)
                        await hb_session.commit()
            except asyncio.CancelledError:
                raise
            except Exception as hb_err:
                logger.warning(
                    "Re-embedding heartbeat DB touch failed (non-fatal)",
                    extra={"knowledge_base_id": knowledge_base_id, "error": str(hb_err)},
                )

            try:
                from .core.queue_backend import get_queue_backend

                queue = await get_queue_backend()
                extended = await queue.extend_visibility(job, additional_seconds=interval * 2)
                if not extended:
                    logger.warning(
                        "extend_visibility returned False — re-embedding job may have been re-delivered",
                        extra={"knowledge_base_id": knowledge_base_id, "job_id": job.id},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as ev_err:
                logger.warning(
                    "Re-embedding extend_visibility failed (non-fatal)",
                    extra={"knowledge_base_id": knowledge_base_id, "job_id": job.id, "error": str(ev_err)},
                )
    except asyncio.CancelledError:
        pass


async def recover_interrupted_re_embedding_jobs(queue_backend) -> int:
    """Re-enqueue re-embedding jobs lost due to queue backend restart.

    With a persistent Redis backend the job is still in the queue or
    processing set and will be redelivered automatically, so we only
    re-enqueue KBs whose heartbeat appears stale (no updated_at touch
    within ``STALE_AFTER``).

    Returns the number of KBs whose jobs were re-enqueued.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from .core.database import get_async_session_local
    from .core.workload_routing import WorkloadType, enqueue_job
    from .models.knowledge_base import KnowledgeBase

    stale_after = timedelta(minutes=3)
    resumed_count = 0
    session_factory = get_async_session_local()

    async with session_factory() as session:
        result = await session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.embedding_status == "re_embedding")
            .with_for_update(skip_locked=True)
        )
        stuck_kbs = list(result.scalars().all())

        for kb in stuck_kbs:
            last_updated = kb.updated_at
            if last_updated is not None and last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=UTC)
            is_stale = last_updated is None or (datetime.now(UTC) - last_updated) >= stale_after
            if not is_stale:
                logger.info(
                    "Skipping re-embedding recovery for KB with fresh heartbeat",
                    extra={
                        "knowledge_base_id": str(kb.id),
                        "last_updated": str(last_updated),
                        "stale_after_seconds": int(stale_after.total_seconds()),
                    },
                )
                continue

            progress: dict = kb.re_embedding_progress if kb.re_embedding_progress else {}
            phase = progress.get("phase", "chunks")
            chunks_done = progress.get("chunks_done", 0)
            chunks_total = progress.get("chunks_total", "?")
            workers_total = progress.get("workers_total", 1)
            workers_completed = progress.get("workers_completed", 0)
            logger.info(
                f"Resuming re-embedding for KB {kb.id}, "
                f"phase={phase}, chunks_done={chunks_done}/{chunks_total}, "
                f"workers={workers_completed}/{workers_total}"
            )

            if phase == "chunks":
                # Re-enqueue chunk jobs for remaining work
                remaining_workers = max(1, workers_total - workers_completed)

                for i in range(remaining_workers):
                    await enqueue_job(
                        queue_backend,
                        WorkloadType.RE_EMBEDDING,
                        payload={
                            "knowledge_base_id": str(kb.id),
                            "action": "re_embed_chunks",
                            "worker_index": i,
                            "workers_total": remaining_workers,
                        },
                        max_attempts=3,
                        visibility_timeout=600,
                    )

                # Commit progress update only after all jobs are enqueued so a
                # mid-loop failure doesn't leave workers_total out of sync with
                # actual queue state.  Create a new dict so SQLAlchemy detects the
                # change (plain JSON column, no MutableDict).
                kb.re_embedding_progress = {
                    **progress,
                    "workers_completed": 0,
                    "workers_total": remaining_workers,
                }
                await session.commit()
            else:
                # Past chunks phase — enqueue finalization only
                await enqueue_job(
                    queue_backend,
                    WorkloadType.RE_EMBEDDING,
                    payload={
                        "knowledge_base_id": str(kb.id),
                        "action": "re_embed_finalize",
                    },
                    max_attempts=3,
                    visibility_timeout=600,
                )
                await session.commit()
            resumed_count += 1

    return resumed_count


async def handle_re_embedding_job(job) -> None:
    """Route RE_EMBEDDING jobs to the appropriate handler based on action."""
    action = job.payload.get("action", "re_embed_chunks")

    if action == "re_embed_chunks":
        await _handle_re_embed_chunks_job(job)
    elif action == "re_embed_finalize":
        await _handle_re_embed_finalize_job(job)
    else:
        raise ValueError(f"Unknown re-embedding action: {action}")


async def _handle_re_embed_chunks_job(job) -> None:  # noqa: PLR0912, PLR0915
    """Handle a chunk re-embedding job.

    Multiple instances run in parallel, each competing for unprocessed chunks
    via FOR UPDATE SKIP LOCKED. When a worker finds no more chunks to process,
    it atomically increments the completion counter. The last worker to finish
    enqueues the finalization job.

    Individual worker failures do NOT mark the KB as error — other workers
    will absorb the remaining work. Only if ALL workers exhaust retries and
    unprocessed chunks still remain will the last worker to complete detect
    the problem and mark the KB as error.
    """
    from datetime import UTC, datetime

    from sqlalchemy import func, or_, select

    from .core.database import get_async_session_local
    from .core.embedding_service import get_embedding_service
    from .core.vector_store import VectorEntry, get_vector_store
    from .models.document import DocumentChunk
    from .models.knowledge_base import KnowledgeBase

    knowledge_base_id = job.payload.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("RE_EMBEDDING job missing knowledge_base_id in payload")

    worker_index = job.payload.get("worker_index", 0)

    logger.info(
        "Processing re-embedding chunk worker",
        extra={
            "job_id": job.id,
            "knowledge_base_id": knowledge_base_id,
            "worker_index": worker_index,
        },
    )

    from .core.config import get_settings_instance

    settings = get_settings_instance()
    embedding_service = await get_embedding_service()
    vector_store = await get_vector_store()
    target_model = embedding_service.model_name

    session_local = get_async_session_local()

    async with session_local() as session:
        kb = await session.get(KnowledgeBase, knowledge_base_id)
        if kb is None:
            logger.info(
                "Knowledge base deleted, discarding re-embedding job",
                extra={"job_id": job.id, "knowledge_base_id": knowledge_base_id},
            )
            return

        if kb.embedding_status != "re_embedding":
            logger.warning(
                "KB embedding_status is not 're_embedding', skipping",
                extra={
                    "job_id": job.id,
                    "knowledge_base_id": knowledge_base_id,
                    "embedding_status": kb.embedding_status,
                },
            )
            return

        heartbeat_task = asyncio.create_task(_re_embedding_heartbeat(job, knowledge_base_id))

        try:
            import time as _time

            chunks_done_this_job = 0

            # Competing consumers: grab next batch with FOR UPDATE SKIP LOCKED
            # so parallel workers never process the same chunks.
            unprocessed_filter = (
                select(DocumentChunk)
                .where(
                    DocumentChunk.knowledge_base_id == knowledge_base_id,
                    or_(
                        DocumentChunk.embedding_model.is_(None),
                        DocumentChunk.embedding_model != target_model,
                        DocumentChunk.embedding.is_(None),
                    ),
                )
                .order_by(DocumentChunk.id)
                .limit(settings.embedding_batch_size)
                .with_for_update(skip_locked=True)
            )

            while True:
                # Check if KB is still in re_embedding status (another worker
                # may have failed and marked it as error)
                await session.refresh(kb, attribute_names=["embedding_status"])
                if kb.embedding_status != "re_embedding":
                    logger.info(
                        "KB no longer in re_embedding status, stopping worker",
                        extra={
                            "job_id": job.id,
                            "knowledge_base_id": knowledge_base_id,
                            "embedding_status": kb.embedding_status,
                            "worker_index": worker_index,
                        },
                    )
                    break

                t0 = _time.monotonic()
                result = await session.execute(unprocessed_filter)
                batch = list(result.scalars().all())
                if not batch:
                    break
                t_select = _time.monotonic() - t0

                texts = [chunk.content for chunk in batch]
                t0 = _time.monotonic()
                embeddings = await embedding_service.embed_texts(texts)
                t_embed = _time.monotonic() - t0

                entries = [
                    VectorEntry(id=str(chunk.id), vector=emb) for chunk, emb in zip(batch, embeddings, strict=True)
                ]
                t0 = _time.monotonic()
                await vector_store.store_embeddings("chunks", entries, db=session)
                t_store = _time.monotonic() - t0

                now = datetime.now(UTC)
                for chunk in batch:
                    chunk.embedding_model = target_model
                    chunk.embedding_created_at = now

                chunks_done_this_job += len(batch)

                # Atomic progress update via row lock.
                # populate_existing=True forces a DB refresh past the identity map.
                kb_locked = await session.execute(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.id == knowledge_base_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                kb_for_progress = kb_locked.scalar_one()
                kb_for_progress.increment_re_embedding_progress(len(batch))
                await session.commit()

                # Refresh kb reference after commit
                await session.refresh(kb)

                logger.debug(
                    "Re-embedded chunk batch",
                    extra={
                        "knowledge_base_id": knowledge_base_id,
                        "worker_index": worker_index,
                        "batch_size": len(batch),
                        "chunks_done_this_job": chunks_done_this_job,
                        "select_ms": round(t_select * 1000, 1),
                        "embed_ms": round(t_embed * 1000, 1),
                        "store_ms": round(t_store * 1000, 1),
                    },
                )

            logger.info(
                "Chunk worker complete",
                extra={
                    "job_id": job.id,
                    "knowledge_base_id": knowledge_base_id,
                    "worker_index": worker_index,
                    "chunks_processed": chunks_done_this_job,
                },
            )

            # Atomically increment workers_completed; last one enqueues finalization.
            # IMPORTANT: populate_existing=True forces SQLAlchemy to refresh
            # from the DB row rather than returning the stale identity-map object.
            # Without it, concurrent workers all read the same pre-lock value.
            kb_locked = await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.id == knowledge_base_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            kb_final = kb_locked.scalar_one()
            all_done = kb_final.increment_workers_completed()

            if all_done:
                # Check if any chunks still need processing (all workers may
                # have failed on some chunks that none could handle)
                remaining = await session.execute(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.knowledge_base_id == knowledge_base_id,
                        or_(
                            DocumentChunk.embedding_model.is_(None),
                            DocumentChunk.embedding_model != target_model,
                            DocumentChunk.embedding.is_(None),
                        ),
                    )
                )
                remaining_count = remaining.scalar() or 0

                if remaining_count > 0:
                    kb_final.mark_re_embedding_failed(
                        f"{remaining_count} chunks remain unprocessed after all workers completed"
                    )
                    await session.commit()
                    logger.error(
                        "Re-embedding incomplete: unprocessed chunks remain after all workers finished",
                        extra={
                            "knowledge_base_id": knowledge_base_id,
                            "remaining_chunks": remaining_count,
                        },
                    )
                else:
                    await session.commit()
                    logger.info(
                        "All chunk workers complete, enqueueing finalization",
                        extra={"knowledge_base_id": knowledge_base_id},
                    )
                    from .core.queue_backend import get_queue_backend
                    from .core.workload_routing import WorkloadType, enqueue_job

                    queue_backend = await get_queue_backend()
                    await enqueue_job(
                        queue_backend,
                        WorkloadType.RE_EMBEDDING,
                        payload={
                            "knowledge_base_id": knowledge_base_id,
                            "action": "re_embed_finalize",
                        },
                        max_attempts=3,
                        visibility_timeout=600,
                    )
            else:
                await session.commit()

        except Exception:
            logger.warning(
                "Re-embedding chunk worker failed",
                extra={
                    "job_id": job.id,
                    "knowledge_base_id": knowledge_base_id,
                    "worker_index": worker_index,
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                },
                exc_info=True,
            )

            if job.attempts >= job.max_attempts:
                # Worker exhausted retries — must still increment
                # workers_completed so finalization isn't permanently
                # blocked. Use a fresh session since the current one
                # may be in a bad state from the exception.
                try:
                    err_session_local = get_async_session_local()
                    async with err_session_local() as err_session:
                        kb_locked = await err_session.execute(
                            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id).with_for_update()
                        )
                        kb_err = kb_locked.scalar_one_or_none()
                        if kb_err and kb_err.embedding_status == "re_embedding":
                            all_done = kb_err.increment_workers_completed()
                            enqueue_finalize = False

                            if all_done:
                                # Last worker — check for unprocessed chunks
                                remaining = await err_session.execute(
                                    select(func.count(DocumentChunk.id)).where(
                                        DocumentChunk.knowledge_base_id == knowledge_base_id,
                                        or_(
                                            DocumentChunk.embedding_model.is_(None),
                                            DocumentChunk.embedding_model != target_model,
                                            DocumentChunk.embedding.is_(None),
                                        ),
                                    )
                                )
                                remaining_count = remaining.scalar() or 0

                                if remaining_count > 0:
                                    kb_err.mark_re_embedding_failed(
                                        f"{remaining_count} chunks remain unprocessed after all workers completed"
                                    )
                                else:
                                    enqueue_finalize = True

                            # Commit DB changes (workers_completed increment,
                            # possible error marking) before enqueueing so the
                            # finalization job only runs against durable state.
                            await err_session.commit()

                            if enqueue_finalize:
                                # All chunks done despite this worker failing —
                                # other workers absorbed the work. Enqueue finalization.
                                from .core.queue_backend import get_queue_backend
                                from .core.workload_routing import WorkloadType, enqueue_job

                                queue_backend = await get_queue_backend()
                                await enqueue_job(
                                    queue_backend,
                                    WorkloadType.RE_EMBEDDING,
                                    payload={
                                        "knowledge_base_id": knowledge_base_id,
                                        "action": "re_embed_finalize",
                                    },
                                    max_attempts=3,
                                    visibility_timeout=600,
                                )
                except Exception:
                    logger.error(
                        "Failed to increment workers_completed for exhausted worker",
                        extra={
                            "knowledge_base_id": knowledge_base_id,
                            "worker_index": worker_index,
                        },
                        exc_info=True,
                    )

            raise

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


async def _handle_re_embed_finalize_job(job) -> None:  # noqa: PLR0912, PLR0915
    """Handle the finalization phase of re-embedding.

    Runs after all chunk workers complete. Processes synopses, queries,
    and indexes, then marks the KB as complete.
    """
    from sqlalchemy import select

    from .core.config import get_settings_instance
    from .core.database import get_async_session_local
    from .core.embedding_service import get_embedding_service
    from .core.vector_store import VectorEntry, get_vector_store
    from .models.document import Document, DocumentQuery
    from .models.knowledge_base import KnowledgeBase

    settings = get_settings_instance()
    knowledge_base_id = job.payload.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("RE_EMBEDDING finalize job missing knowledge_base_id in payload")

    logger.info(
        "Processing re-embedding finalization",
        extra={"job_id": job.id, "knowledge_base_id": knowledge_base_id},
    )

    embedding_service = await get_embedding_service()
    vector_store = await get_vector_store()
    target_model = embedding_service.model_name
    target_dimension = embedding_service.dimension

    session_local = get_async_session_local()

    async with session_local() as session:
        kb = await session.get(KnowledgeBase, knowledge_base_id)
        if kb is None:
            logger.info(
                "Knowledge base deleted, discarding finalization job",
                extra={"job_id": job.id, "knowledge_base_id": knowledge_base_id},
            )
            return

        if kb.embedding_status != "re_embedding":
            logger.warning(
                "KB embedding_status is not 're_embedding', skipping finalization",
                extra={
                    "job_id": job.id,
                    "knowledge_base_id": knowledge_base_id,
                    "embedding_status": kb.embedding_status,
                },
            )
            return

        heartbeat_task = asyncio.create_task(_re_embedding_heartbeat(job, knowledge_base_id))

        try:
            synopses_count = 0
            queries_count = 0

            # --- Phase: synopses ---
            kb.update_re_embedding_phase("synopses")
            await session.commit()

            synopses_base = (
                select(Document)
                .where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.synopsis.is_not(None),
                    Document.synopsis != "",
                )
                .order_by(Document.id)
                .limit(settings.embedding_batch_size)
            )

            offset = 0
            while True:
                result = await session.execute(synopses_base.offset(offset))
                batch = list(result.scalars().all())
                if not batch:
                    break

                synopses_count += len(batch)
                texts = [doc.synopsis for doc in batch]
                embeddings = await embedding_service.embed_texts(texts)

                entries = [VectorEntry(id=str(doc.id), vector=emb) for doc, emb in zip(batch, embeddings, strict=True)]
                await vector_store.store_embeddings("synopses", entries, db=session)
                await session.commit()
                offset += len(batch)

            if synopses_count:
                logger.info(
                    "Re-embedded synopses",
                    extra={"knowledge_base_id": knowledge_base_id, "count": synopses_count},
                )

            # --- Phase: queries ---
            kb.update_re_embedding_phase("queries")
            await session.commit()

            queries_base = (
                select(DocumentQuery)
                .where(DocumentQuery.knowledge_base_id == knowledge_base_id)
                .order_by(DocumentQuery.id)
                .limit(settings.embedding_batch_size)
            )

            offset = 0
            while True:
                result = await session.execute(queries_base.offset(offset))
                batch = list(result.scalars().all())
                if not batch:
                    break

                queries_count += len(batch)
                texts = [q.query_text for q in batch]
                embeddings = await embedding_service.embed_queries(texts)

                entries = [VectorEntry(id=str(q.id), vector=emb) for q, emb in zip(batch, embeddings, strict=True)]
                await vector_store.store_embeddings("queries", entries, db=session)
                await session.commit()
                offset += len(batch)

            if queries_count:
                logger.info(
                    "Re-embedded queries",
                    extra={"knowledge_base_id": knowledge_base_id, "count": queries_count},
                )

            # --- Phase: indexes ---
            kb.update_re_embedding_phase("indexes")
            await session.commit()

            await vector_store.ensure_index("chunks", target_dimension, db=session)
            await vector_store.ensure_index("synopses", target_dimension, db=session)
            await vector_store.ensure_index("queries", target_dimension, db=session)
            await session.commit()

            # --- Mark complete ---
            kb.mark_re_embedding_complete(target_model)
            await session.commit()

            logger.info(
                "Re-embedding complete",
                extra={
                    "job_id": job.id,
                    "knowledge_base_id": knowledge_base_id,
                    "synopses_processed": synopses_count,
                    "queries_processed": queries_count,
                    "model": target_model,
                    "dimension": target_dimension,
                },
            )

        except Exception as e:
            if job.attempts >= job.max_attempts:
                # Use a fresh session — the current one may be in a bad
                # state if the exception was a DB error.
                try:
                    err_session_local = get_async_session_local()
                    async with err_session_local() as err_session:
                        kb_locked = await err_session.execute(
                            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id).with_for_update()
                        )
                        kb_err = kb_locked.scalar_one_or_none()
                        if kb_err:
                            kb_err.mark_re_embedding_failed(str(e))
                            await err_session.commit()
                except Exception:
                    logger.error(
                        "Failed to mark KB as error after finalization failure",
                        extra={"knowledge_base_id": knowledge_base_id},
                        exc_info=True,
                    )

                logger.error(
                    "Re-embedding finalization failed after max attempts",
                    extra={
                        "job_id": job.id,
                        "knowledge_base_id": knowledge_base_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": str(e),
                    },
                )
            else:
                logger.warning(
                    "Re-embedding finalization failed, will retry",
                    extra={
                        "job_id": job.id,
                        "knowledge_base_id": knowledge_base_id,
                        "attempts": job.attempts,
                        "max_attempts": job.max_attempts,
                        "error": str(e),
                    },
                )
            raise

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
