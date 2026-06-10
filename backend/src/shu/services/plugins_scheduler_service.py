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

from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

from ..core.config import get_settings_instance
from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_feed import PluginFeed
from ..plugins.registry import REGISTRY
from .plugin_execution import plugin_dispatch_allowed
from .plugin_execution_runner import ONE_SHOT_FEED_PARAMS  # noqa: F401
from .scheduler_service import TICK_HISTORY  # noqa: F401

logger = get_logger(__name__)


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

        Returns a tally keyed by outcome (see `_claim_one_due_schedule`).
        """
        from ..core.queue_backend import get_queue_backend

        due_scheds = await self._claim_due_schedules(limit)
        degraded_mcp = await self._get_degraded_mcp_connections(due_scheds)

        # Get queue backend for job enqueueing; fall back to DB-only if unavailable.
        try:
            queue_backend = await get_queue_backend()
        except Exception as e:
            logger.error(f"Failed to get queue backend: {e}")
            queue_backend = None

        counts: Counter[str] = Counter()
        for s in due_scheds:
            outcome = await self._claim_one_due_schedule(
                s,
                degraded_mcp=degraded_mcp,
                fallback_user_id=fallback_user_id,
                queue_backend=queue_backend,
            )
            counts[outcome] += 1

        await self.db.commit()
        # `queue_enqueued` is the subset of enqueued executions that reached the
        # queue; both outcomes count toward the `enqueued` total.
        return {
            "due": len(due_scheds),
            "enqueued": counts["enqueued"] + counts["queue_enqueued"],
            "queue_enqueued": counts["queue_enqueued"],
            "skipped_no_owner": counts["skipped_no_owner"],
            "skipped_missing_plugin": counts["skipped_missing_plugin"],
            "skipped_already_enqueued": counts["skipped_already_enqueued"],
            "skipped_degraded": counts["skipped_degraded"],
            "skipped_entitlement": counts["skipped_entitlement"],
        }

    async def _claim_due_schedules(self, limit: int | None) -> list[PluginFeed]:
        """Select enabled, due feeds with row-level locks (SKIP LOCKED) so
        concurrent schedulers/workers don't claim the same feed.
        """
        q = (
            select(PluginFeed)
            .where(
                and_(
                    PluginFeed.enabled == True,  # noqa: E712
                    or_(PluginFeed.next_run_at.is_(None), PluginFeed.next_run_at <= datetime.now(UTC)),
                )
            )
            .with_for_update(skip_locked=True)
        )
        if limit and limit > 0:
            q = q.limit(int(limit))
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def _claim_one_due_schedule(
        self,
        s: PluginFeed,
        *,
        degraded_mcp: set[str],
        fallback_user_id: str | None,
        queue_backend,
    ) -> str:
        """Process one due feed and return its outcome key.

        Advances the schedule window on every outcome except `skipped_missing_plugin`
        and `skipped_no_owner` (which leave it due so a fixed feed retries). On a
        successful enqueue returns `queue_enqueued` when the job reached the queue,
        else `enqueued`.
        """
        if s.plugin_name in degraded_mcp:
            logger.warning("Skipping feed '%s': MCP connection is degraded", s.plugin_name)
            s.schedule_next()
            return "skipped_degraded"

        # SHU-773: skip feeds the tenant is no longer entitled to run (e.g. an
        # mcp: feed after mcp_servers is revoked) so we don't churn executions
        # the runner would only reject.
        if not await plugin_dispatch_allowed(s.plugin_name):
            logger.info("Skipping feed '%s': plugin entitlement revoked", s.plugin_name)
            s.schedule_next()
            return "skipped_entitlement"

        plugin = await REGISTRY.resolve(s.plugin_name, self.db)
        if not plugin:
            return "skipped_missing_plugin"

        has_owner = bool(s.owner_user_id)
        if not has_owner and not fallback_user_id:
            return "skipped_no_owner"
        runner_user_id = str(s.owner_user_id) if has_owner else str(fallback_user_id)

        if await self._has_active_execution(s):
            # Advance the window anyway to avoid tight loops; next tick will enqueue.
            s.schedule_next()
            return "skipped_already_enqueued"

        queued = await self._create_and_enqueue_execution(s, runner_user_id, queue_backend)
        s.schedule_next()
        return "queue_enqueued" if queued else "enqueued"

    async def _has_active_execution(self, schedule: PluginFeed) -> bool:
        """Return True if a PENDING/RUNNING execution already exists for this schedule.

        Best-effort idempotency guard — on query error returns False so the
        caller falls through to enqueue rather than silently dropping the run.
        """
        try:
            q = (
                select(PluginExecution.id)
                .where(
                    (PluginExecution.schedule_id == schedule.id)
                    & (PluginExecution.status.in_([PluginExecutionStatus.PENDING, PluginExecutionStatus.RUNNING]))
                )
                .limit(1)
            )
            res = await self.db.execute(q)
            return res.scalar_one_or_none() is not None
        except Exception:
            return False

    async def _get_degraded_mcp_connections(self, due_scheds: list) -> set[str]:
        """Batch-check MCP connection health and return degraded plugin names."""
        from ..models.mcp_server_connection import McpServerConnection

        mcp_names = {s.plugin_name.removeprefix("mcp:") for s in due_scheds if s.plugin_name.startswith("mcp:")}
        if not mcp_names:
            return set()
        q = select(McpServerConnection.name, McpServerConnection.consecutive_failures).where(
            McpServerConnection.name.in_(mcp_names)
        )
        return {f"mcp:{name}" for name, failures in (await self.db.execute(q)).all() if (failures or 0) >= 5}

    async def _create_and_enqueue_execution(self, schedule, user_id: str, queue_backend) -> bool:
        """Create a PluginExecution record and enqueue a job. Returns True if queued."""
        from ..core.workload_routing import WorkloadType, enqueue_job

        exec_rec = PluginExecution(
            schedule_id=schedule.id,
            plugin_name=schedule.plugin_name,
            user_id=user_id,
            agent_key=schedule.agent_key,
            params=schedule.params or {},
            status=PluginExecutionStatus.PENDING,
        )
        self.db.add(exec_rec)
        await self.db.flush()

        if not queue_backend:
            return False
        try:
            job = await enqueue_job(
                queue_backend,
                WorkloadType.INGESTION,
                payload={
                    "action": "plugin_feed_execution",
                    "execution_id": str(exec_rec.id),
                    "schedule_id": str(schedule.id),
                    "plugin_name": schedule.plugin_name,
                    "user_id": user_id,
                    "agent_key": schedule.agent_key,
                    "params": schedule.params or {},
                },
                max_attempts=3,
                visibility_timeout=3600,
            )
            logger.debug(
                "Scheduler job enqueued to queue",
                extra={"execution_id": exec_rec.id, "schedule_id": schedule.id, "job_id": job.id},
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to enqueue job to queue: {e}", extra={"execution_id": exec_rec.id, "schedule_id": schedule.id}
            )
            return False

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
