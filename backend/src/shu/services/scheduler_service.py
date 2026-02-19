"""Unified Scheduler Service.

Single background scheduler that polls multiple schedulable sources per tick,
enqueuing jobs to the appropriate queue for worker processing. This replaces
the separate PluginsSchedulerService and ExperiencesSchedulerService scheduler
loops with one horizontally-safe implementation.

Sources:
- PluginFeedSource: queries PluginFeed rows, enqueues INGESTION jobs
- ExperienceSource: queries Experience rows, fans out per user, enqueues LLM_WORKFLOW jobs

All sources use FOR UPDATE SKIP LOCKED for safe multi-replica operation.
The scheduler does no heavy work — all execution happens in workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..core.database import get_db_session
from ..core.queue_backend import QueueBackend

logger = logging.getLogger(__name__)

# In-memory per-process tick history for observability
TICK_HISTORY: deque[dict[str, Any]] = deque(maxlen=500)


class SchedulableSource(Protocol):
    """Interface for things the unified scheduler can poll and enqueue."""

    @property
    def name(self) -> str:
        """Human-readable source name for logging."""
        ...

    async def cleanup_stale(self, db: AsyncSession) -> int:
        """Clean up stale/orphaned items before enqueueing.

        Returns the number of items cleaned up.
        """
        ...

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        """Find due items, create tracking records, enqueue jobs.

        Returns a dict of counters (source-specific keys).
        """
        ...


class PluginFeedSource:
    """Schedulable source for plugin feeds.

    Wraps the existing PluginsSchedulerService logic for claiming due
    PluginFeed rows and enqueueing INGESTION jobs.
    """

    @property
    def name(self) -> str:
        return "plugin_feeds"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        from .plugins_scheduler_service import PluginsSchedulerService

        svc = PluginsSchedulerService(db)
        return await svc.cleanup_stale_executions()

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        from .plugins_scheduler_service import PluginsSchedulerService

        svc = PluginsSchedulerService(db)
        return await svc.enqueue_due_schedules(limit=limit)


class ExperienceSource:
    """Schedulable source for experiences.

    Queries due Experience rows with FOR UPDATE SKIP LOCKED, fans out
    one LLM_WORKFLOW job per active user per experience, and advances
    the schedule.
    """

    @property
    def name(self) -> str:
        return "experiences"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        # Experiences don't have a stale execution cleanup mechanism yet.
        # Queued runs that are never picked up will remain in "queued" status;
        # a future enhancement could mark old queued runs as failed.
        return 0

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        from sqlalchemy import and_, select
        from sqlalchemy.orm import selectinload

        from ..auth.models import User
        from ..core.workload_routing import WorkloadType, enqueue_job
        from ..models.experience import Experience, ExperienceRun
        from ..models.user_preferences import UserPreferences

        now = datetime.now(UTC)

        # Claim due experiences with row-level locks
        stmt = (
            select(Experience)
            .options(selectinload(Experience.steps))
            .where(
                and_(
                    Experience.trigger_type.in_(["scheduled", "cron"]),
                    Experience.visibility.in_(["published", "admin_only"]),
                    Experience.next_run_at <= now,
                )
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        result = await db.execute(stmt)
        due_experiences = list(result.scalars().all())

        if not due_experiences:
            return {"due": 0, "enqueued": 0, "queue_enqueued": 0, "no_users": 0}

        # Get all active users once
        user_result = await db.execute(
            select(User).where(User.is_active == True)  # noqa: E712
        )
        all_users = list(user_result.scalars().all())

        if not all_users:
            # No users — still advance schedules so we don't tight-loop.
            # Set last_run_at so that one-time "scheduled" experiences see
            # they have already been processed and schedule_next() clears
            # next_run_at instead of leaving it in the past.
            for exp in due_experiences:
                exp.last_run_at = now
                exp.schedule_next()
            await db.commit()
            return {
                "due": len(due_experiences),
                "enqueued": 0,
                "queue_enqueued": 0,
                "no_users": 1,
            }

        enqueued = 0
        queue_enqueued = 0

        for exp in due_experiences:
            # Fan out: one job per active user
            for user in all_users:
                run = None
                try:
                    # Create ExperienceRun in QUEUED status for observability
                    run = ExperienceRun(
                        experience_id=str(exp.id),
                        user_id=str(user.id),
                        model_configuration_id=exp.model_configuration_id,
                        status="queued",
                        input_params={},
                        step_states={},
                        step_outputs={},
                        result_metadata={},
                    )
                    db.add(run)
                    await db.flush()  # Get run.id for the job payload

                    job = await enqueue_job(
                        queue,
                        WorkloadType.LLM_WORKFLOW,
                        payload={
                            "action": "experience_execution",
                            "experience_id": str(exp.id),
                            "user_id": str(user.id),
                            "run_id": str(run.id),
                            "input_params": {},
                        },
                        max_attempts=3,
                        visibility_timeout=600,  # 10 min for LLM work
                    )
                    queue_enqueued += 1
                    logger.debug(
                        "Experience job enqueued",
                        extra={
                            "experience_id": exp.id,
                            "user_id": user.id,
                            "run_id": run.id,
                            "job_id": job.id,
                        },
                    )
                except Exception as e:
                    # Remove the flushed run so it doesn't get committed
                    # as an orphaned "queued" record with no corresponding job.
                    if run is not None:
                        await db.delete(run)
                        await db.flush()
                    logger.error(
                        "Failed to enqueue experience job: %s",
                        e,
                        extra={
                            "experience_id": exp.id,
                            "user_id": user.id,
                        },
                    )

            enqueued += 1

            # Advance schedule ONCE per experience (not per user)
            # Use creator's timezone for scheduling
            creator_tz = None
            if exp.created_by:
                try:
                    tz_result = await db.execute(
                        select(UserPreferences).where(UserPreferences.user_id == exp.created_by)
                    )
                    prefs = tz_result.scalar_one_or_none()
                    creator_tz = prefs.timezone if prefs else None
                except Exception:
                    pass

            exp.last_run_at = now
            exp.schedule_next(user_timezone=creator_tz)

        await db.commit()

        logger.info(
            "Experience source tick | due=%d enqueued=%d queue_enqueued=%d users=%d",
            len(due_experiences),
            enqueued,
            queue_enqueued,
            len(all_users),
        )

        return {
            "due": len(due_experiences),
            "enqueued": enqueued,
            "queue_enqueued": queue_enqueued,
            "no_users": 0,
        }


class LogMaintenanceSource:
    """Schedulable source for log file maintenance.

    Runs on every scheduler tick (typically every 60s). Handles:
    - Midnight rotation: archives the current log file when the UTC date changes.
    - Retention cleanup: prunes archived log files older than the configured window.

    This is a filesystem-only operation — no DB or queue interaction needed.
    Each replica manages its own hostname-prefixed log files independently.
    """

    @property
    def name(self) -> str:
        return "log_maintenance"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        from ..core.logging import get_managed_file_handler

        handler = get_managed_file_handler()
        if handler is not None:
            handler.rotate_if_needed()
        return 0

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        # Nothing to enqueue — all work happens in cleanup_stale
        return {"enqueued": 0}


class IngestionStagingMaintenanceSource:
    """Schedulable source for ingestion staging directory cleanup.

    Runs on every scheduler tick (typically every 60s). Scans
    ``SHU_INGESTION_STAGING_DIR`` and deletes files older than
    ``SHU_INGESTION_STAGING_MAX_AGE_HOURS``.

    Orphans arise when a worker is OOMKilled or pod-evicted mid-job before it
    can delete the staged file.  This sweep ensures disk space is reclaimed
    without operator intervention.

    This is a filesystem-only operation — no DB or queue interaction needed.
    """

    @property
    def name(self) -> str:
        return "ingestion_staging_maintenance"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        import time

        from ..core.config import get_settings_instance

        settings = get_settings_instance()
        staging_dir = settings.ingestion_staging_dir
        max_age_seconds = settings.ingestion_staging_max_age_hours * 3600
        cutoff = time.time() - max_age_seconds

        if not Path(staging_dir).is_dir():
            return 0

        deleted = 0
        try:
            with os.scandir(staging_dir) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    try:
                        if entry.stat().st_mtime < cutoff:
                            os.unlink(entry.path)
                            deleted += 1
                            logger.info(
                                "Deleted orphaned staging file",
                                extra={"path": entry.path},
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to delete orphaned staging file",
                            extra={"path": entry.path, "error": str(e)},
                        )
        except Exception as e:
            logger.warning("Ingestion staging sweep failed", extra={"error": str(e)})

        return deleted

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        # Nothing to enqueue — all work happens in cleanup_stale
        return {"enqueued": 0}


class UnifiedSchedulerService:
    """Unified scheduler that iterates over registered sources per tick."""

    def __init__(
        self,
        db: AsyncSession,
        queue: QueueBackend,
        sources: list[SchedulableSource],
    ) -> None:
        self.db = db
        self.queue = queue
        self.sources = sources

    async def tick(self, *, limit: int = 10) -> dict[str, Any]:
        """Run one scheduler tick across all sources.

        For each source: cleanup stale items, then enqueue due items.
        Returns a summary dict keyed by source name.
        """
        results: dict[str, Any] = {}

        for source in self.sources:
            try:
                stale_cleaned = await source.cleanup_stale(self.db)
                enqueue_result = await source.enqueue_due(self.db, self.queue, limit=limit)
                results[source.name] = {
                    "stale_cleaned": stale_cleaned,
                    **enqueue_result,
                }
            except Exception as e:
                logger.warning("Scheduler source '%s' failed: %s", source.name, e)
                results[source.name] = {"error": str(e)}

        return results


async def start_scheduler() -> asyncio.Task:
    """Start the unified scheduler background task.

    Replaces both start_plugins_scheduler() and start_experiences_scheduler()
    with a single loop that polls all registered sources.
    """
    from ..core.queue_backend import get_queue_backend

    settings = get_settings_instance()

    # Check if scheduler is enabled
    enabled_env = os.getenv("SHU_SCHEDULER_ENABLED")
    if enabled_env is not None:
        enabled = enabled_env.lower() in ("1", "true", "yes", "on")
    else:
        # Fall back to legacy plugins_scheduler_enabled for backward compat
        enabled = getattr(settings, "plugins_scheduler_enabled", True)

    if not enabled:
        logger.info("Unified scheduler disabled by configuration")
        return asyncio.create_task(asyncio.sleep(0), name="scheduler:disabled")

    tick_interval = max(
        1,
        int(
            os.getenv(
                "SHU_SCHEDULER_TICK_SECONDS",
                os.getenv(
                    "SHU_PLUGINS_SCHEDULER_TICK_SECONDS",
                    str(getattr(settings, "plugins_scheduler_tick_seconds", 60)),
                ),
            )
        ),
    )

    batch_limit = max(
        1,
        int(
            os.getenv(
                "SHU_SCHEDULER_BATCH_LIMIT",
                os.getenv(
                    "SHU_PLUGINS_SCHEDULER_BATCH_LIMIT",
                    str(getattr(settings, "plugins_scheduler_batch_limit", 10)),
                ),
            )
        ),
    )

    # Build source list based on configuration
    sources: list[SchedulableSource] = []

    # Plugin feeds source (always included when scheduler is enabled)
    sources.append(PluginFeedSource())

    # Experiences source (can be independently disabled)
    experiences_enabled_env = os.getenv("SHU_EXPERIENCES_SCHEDULER_ENABLED")
    if experiences_enabled_env is not None:
        experiences_enabled = experiences_enabled_env.lower() in ("1", "true", "yes", "on")
    else:
        experiences_enabled = getattr(settings, "experiences_scheduler_enabled", True)

    if experiences_enabled:
        sources.append(ExperienceSource())
    else:
        logger.info("Experiences source disabled by configuration")

    # Log maintenance source: midnight rotation + retention cleanup (always enabled)
    sources.append(LogMaintenanceSource())

    # Ingestion staging maintenance: orphan file cleanup (always enabled)
    sources.append(IngestionStagingMaintenanceSource())

    logger.info(
        "Starting unified scheduler | tick=%ds batch=%d sources=%s",
        tick_interval,
        batch_limit,
        [s.name for s in sources],
    )

    async def _runner() -> None:
        while True:
            try:
                queue = await get_queue_backend()
                db = await get_db_session()
                async with db as session:
                    svc = UnifiedSchedulerService(session, queue, sources)
                    results = await svc.tick(limit=batch_limit)

                    # Log if any source had activity
                    has_activity = any(
                        r.get("enqueued", 0) > 0 or r.get("stale_cleaned", 0) > 0
                        for r in results.values()
                        if isinstance(r, dict) and "error" not in r
                    )
                    if has_activity:
                        logger.info("Scheduler tick | %s", results)
                        try:
                            TICK_HISTORY.append(
                                {
                                    "ts": datetime.now(UTC).isoformat(),
                                    **results,
                                }
                            )
                        except Exception:
                            pass
            except Exception as ex:
                logger.warning("Scheduler tick failed: %s", ex)
            finally:
                try:
                    await asyncio.sleep(tick_interval)
                except asyncio.CancelledError:
                    logger.info("Unified scheduler stopped")
                    break

    return asyncio.create_task(_runner(), name="scheduler:unified")
