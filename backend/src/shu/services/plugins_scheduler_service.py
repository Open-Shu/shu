"""Plugin Feeds Scheduler service.

- Enqueue due PluginFeed rows into QueueBackend as jobs with INGESTION WorkloadType
- Workers dequeue and process jobs from the queue
- PluginExecution records track execution state for idempotency and observability

DRY: API endpoints delegate to this service.
Concurrency: uses QueueBackend for job distribution and SELECT ... FOR UPDATE SKIP LOCKED
for database-level idempotency guards.

The scheduler loop has been moved to scheduler_service.py (UnifiedSchedulerService).
This module retains PluginsSchedulerService for use by the unified scheduler's
PluginFeedSource and by API manual-trigger endpoints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_feed import PluginFeed
from ..plugins.registry import REGISTRY
from .plugin_execution_runner import ONE_SHOT_FEED_PARAMS  # noqa: F401
from .scheduler_service import TICK_HISTORY  # noqa: F401

logger = logging.getLogger(__name__)


class PluginsSchedulerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings_instance()

    async def enqueue_due_schedules(
        self, *, limit: int | None = None, fallback_user_id: str | None = None
    ) -> dict[str, int]:
        """Atomically claim due schedules and enqueue executions to QueueBackend.

        Creates PluginExecution records for tracking and idempotency, then enqueues
        jobs to QueueBackend with INGESTION WorkloadType for worker processing.

        Returns: {"due": n, "enqueued": m, "skipped_no_owner": k, "skipped_missing_plugin": j, "queue_enqueued": p}
        """
        from ..core.queue_backend import get_queue_backend
        from ..core.workload_routing import WorkloadType, enqueue_job

        now = datetime.now(UTC)
        # Claim due schedules with row-level locks to avoid duplicates across workers
        q = (
            select(PluginFeed)
            .where(
                and_(
                    PluginFeed.enabled == True,  # noqa: E712
                    or_(PluginFeed.next_run_at.is_(None), PluginFeed.next_run_at <= now),
                )
            )
            .with_for_update(skip_locked=True)
        )
        if limit and limit > 0:
            q = q.limit(int(limit))
        res = await self.db.execute(q)
        due_scheds = list(res.scalars().all())
        enqueued = 0
        queue_enqueued = 0
        skipped_no_owner = 0
        skipped_missing_plugin = 0
        skipped_already_enqueued = 0

        # Get queue backend for job enqueueing
        try:
            queue_backend = await get_queue_backend()
        except Exception as e:
            logger.error(f"Failed to get queue backend: {e}")
            # Fall back to database-only mode if queue is unavailable
            queue_backend = None

        for s in due_scheds:
            # Skip if plugin no longer exists or is disabled
            plugin = await REGISTRY.resolve(s.plugin_name, self.db)
            if not plugin:
                skipped_missing_plugin += 1
                continue

            has_owner = bool(s.owner_user_id)
            if not has_owner and not fallback_user_id:
                # Owner is required for background runs; skip enqueue and count
                skipped_no_owner += 1
                continue
            runner_user_id = str(s.owner_user_id) if has_owner else str(fallback_user_id)

            # Idempotency guard: skip enqueue if an execution is already pending or running for this schedule
            try:
                exists_q = (
                    select(PluginExecution.id)
                    .where(
                        (PluginExecution.schedule_id == s.id)
                        & (PluginExecution.status.in_([PluginExecutionStatus.PENDING, PluginExecutionStatus.RUNNING]))
                    )
                    .limit(1)
                )
                exists_res = await self.db.execute(exists_q)
                if exists_res.scalar_one_or_none() is not None:
                    # Advance schedule window anyway to avoid tight loops; next tick will enqueue
                    s.schedule_next()
                    skipped_already_enqueued += 1
                    continue
            except Exception:
                # Best-effort idempotency guard; fall through to enqueue
                pass

            # Create PluginExecution record for tracking and idempotency
            exec_rec = PluginExecution(
                schedule_id=s.id,
                plugin_name=s.plugin_name,
                user_id=runner_user_id,
                agent_key=s.agent_key,
                params=s.params or {},
                status=PluginExecutionStatus.PENDING,
            )
            self.db.add(exec_rec)
            await self.db.flush()  # Flush to get exec_rec.id

            # Enqueue job to QueueBackend with INGESTION WorkloadType
            if queue_backend:
                try:
                    job = await enqueue_job(
                        queue_backend,
                        WorkloadType.INGESTION,
                        payload={
                            "action": "plugin_feed_execution",
                            "execution_id": str(exec_rec.id),
                            "schedule_id": str(s.id),
                            "plugin_name": s.plugin_name,
                            "user_id": runner_user_id,
                            "agent_key": s.agent_key,
                            "params": s.params or {},
                        },
                        max_attempts=3,
                        visibility_timeout=3600,  # 1 hour for plugin execution
                    )
                    queue_enqueued += 1
                    logger.debug(
                        "Scheduler job enqueued to queue",
                        extra={
                            "execution_id": exec_rec.id,
                            "schedule_id": s.id,
                            "job_id": job.id,
                        },
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to enqueue job to queue: {e}", extra={"execution_id": exec_rec.id, "schedule_id": s.id}
                    )
                    # Continue - the PluginExecution record is created with PENDING status,
                    # and will be picked up by _claim_pending() on the next scheduler cycle.

            # Advance schedule to next time; safe under lock
            s.schedule_next()
            enqueued += 1

        await self.db.commit()
        return {
            "due": len(due_scheds),
            "enqueued": enqueued,
            "queue_enqueued": queue_enqueued,
            "skipped_no_owner": skipped_no_owner,
            "skipped_missing_plugin": skipped_missing_plugin,
            "skipped_already_enqueued": skipped_already_enqueued,
        }

    async def _claim_pending(
        self, *, limit: int, schedule_id: str | None = None, execution_id: str | None = None
    ) -> list[PluginExecution]:
        # Build base query: only claim schedule-backed executions
        now = datetime.now(UTC)
        q = select(PluginExecution).where(
            (PluginExecution.status == PluginExecutionStatus.PENDING)
            & ((PluginExecution.started_at == None) | (PluginExecution.started_at <= now))  # noqa: E711
            & (PluginExecution.schedule_id != None)  # noqa: E711
        )
        if execution_id:
            q = q.where(PluginExecution.id == execution_id)
        elif schedule_id:
            q = q.where(PluginExecution.schedule_id == schedule_id)
        q = q.with_for_update(skip_locked=True)
        if limit and limit > 0:
            q = q.limit(int(limit))
        res = await self.db.execute(q)
        rows = list(res.scalars().all())
        # Mark as RUNNING and stamp started_at in the same txn to release locks quickly
        now = datetime.now(UTC)
        for rec in rows:
            rec.status = PluginExecutionStatus.RUNNING
            rec.started_at = now
        await self.db.commit()
        return rows

    async def cleanup_stale_executions(self) -> int:
        """Mark RUNNING executions with no heartbeat for longer than the configured timeout as FAILED.

        Uses updated_at as the stale cutoff — the worker heartbeat bumps this every 60 s,
        so a healthy long-running plugin is never incorrectly marked stale.

        This must run BEFORE enqueue_due_schedules() so the idempotency guard
        does not skip creating new executions for schedules whose previous
        execution was orphaned by a server restart.

        Returns the number of executions marked as stale.
        """
        stale_cleaned = 0
        try:
            timeout_sec = int(getattr(self.settings, "plugins_scheduler_running_timeout_seconds", 3600))
            if timeout_sec > 0:
                cutoff = datetime.now(UTC) - timedelta(seconds=timeout_sec)
                stale_q = (
                    select(PluginExecution)
                    .where(
                        (PluginExecution.status == PluginExecutionStatus.RUNNING)
                        & (PluginExecution.started_at != None)  # noqa: E711
                        & (PluginExecution.updated_at <= cutoff)
                    )
                    .with_for_update(skip_locked=True)
                )
                res_stale = await self.db.execute(stale_q)
                stale_rows = list(res_stale.scalars().all())
                if stale_rows:
                    now_ts = datetime.now(UTC)
                    for r in stale_rows:
                        r.status = PluginExecutionStatus.FAILED
                        r.error = "stale_timeout"
                        r.completed_at = now_ts
                    await self.db.commit()
                    stale_cleaned = len(stale_rows)
        except Exception:
            # Best-effort cleanup; ignore errors
            pass
        return stale_cleaned

    async def run_pending(
        self, *, limit: int = 10, schedule_id: str | None = None, execution_id: str | None = None
    ) -> dict[str, int]:
        """Claim up to limit pending executions and run them sequentially.
        Returns: {"attempted": n, "ran": m, "failed_owner_required": x, "skipped_disabled": y}.
        """
        from .plugin_execution_runner import execute_plugin_record

        claimed = await self._claim_pending(
            limit=max(1, int(limit)), schedule_id=schedule_id, execution_id=execution_id
        )
        ran = 0
        failed_owner_required = 0
        skipped_disabled = 0
        deferred_429 = 0

        for rec in claimed:
            try:
                result = await execute_plugin_record(self.db, rec, self.settings)
                await self.db.commit()
                if result.error_code == "schedule_disabled":
                    skipped_disabled += 1
                elif result.status == PluginExecutionStatus.COMPLETED:
                    ran += 1
            except HTTPException as he:
                # Gracefully defer on rate/concurrency limiting; do not mark as failed
                code = he.status_code
                detail = he.detail if isinstance(he.detail, dict) else {}
                err = str(detail.get("error") or "")
                if code == 429 and err in (
                    "provider_rate_limited",
                    "provider_concurrency_limited",
                    "rate_limited",
                ):
                    try:
                        ra = None
                        hdrs = getattr(he, "headers", None) or {}
                        ra = hdrs.get("Retry-After") if isinstance(hdrs, dict) else None
                        try:
                            delay = (
                                int(ra)
                                if ra is not None
                                else int(getattr(self.settings, "plugins_scheduler_retry_backoff_seconds", 5))
                            )
                        except Exception:
                            delay = int(getattr(self.settings, "plugins_scheduler_retry_backoff_seconds", 5))
                        logger.info(
                            "Deferred execution due to 429 (%s) | plugin=%s exec_id=%s retry_after=%s",
                            err,
                            rec.plugin_name,
                            rec.id,
                            ra,
                        )
                    except Exception:
                        delay = int(getattr(self.settings, "plugins_scheduler_retry_backoff_seconds", 5))
                    # Requeue by setting back to PENDING and delaying next claim via started_at
                    rec.status = PluginExecutionStatus.PENDING
                    rec.started_at = datetime.now(UTC) + timedelta(seconds=max(1, delay))
                    rec.error = f"deferred:{err}"
                    await self.db.commit()
                    deferred_429 += 1
                    continue
                # Other HTTP errors: mark failed
                logger.exception(
                    "Scheduled plugin execution HTTPException | plugin=%s schedule_id=%s exec_id=%s",
                    rec.plugin_name,
                    rec.schedule_id,
                    rec.id,
                )
                rec.status = PluginExecutionStatus.FAILED
                rec.error = str(he.detail)
                rec.completed_at = datetime.now(UTC)
                await self.db.commit()
            except Exception as e:
                logger.exception(
                    "Scheduled plugin execution failed | plugin=%s schedule_id=%s exec_id=%s",
                    rec.plugin_name,
                    rec.schedule_id,
                    rec.id,
                )
                rec.status = PluginExecutionStatus.FAILED
                rec.error = str(e)
                rec.completed_at = datetime.now(UTC)
                await self.db.commit()
        return {
            "attempted": len(claimed),
            "ran": ran,
            "failed_owner_required": failed_owner_required,
            "skipped_disabled": skipped_disabled,
            "deferred_429": deferred_429,
        }


async def start_plugins_scheduler():
    """Delegate to the unified scheduler (deprecated in favor of start_scheduler).

    Kept for backward compatibility. Delegates to the unified scheduler
    with only the plugin feeds source enabled.
    """
    from .scheduler_service import start_scheduler

    logger.info("start_plugins_scheduler() is deprecated; delegating to unified scheduler")
    return await start_scheduler()
