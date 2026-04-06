"""Experiences Scheduler Service.

In-process scheduler that executes due experiences based on their trigger_type
and trigger_config. Follows the same pattern as PluginsSchedulerService.

- Queries experiences with next_run_at <= now
- Executes them via ExperienceExecutor (non-streaming mode)
- Updates next_run_at using schedule_next() for next execution window
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.auth.models import User
from shu.core.config import get_settings_instance
from shu.core.database import get_db_session
from shu.models.experience import Experience
from shu.models.user_preferences import UserPreferences
from shu.services.policy_engine import POLICY_CACHE

logger = logging.getLogger(__name__)


def _get_linked_experience_ids(experience: Experience) -> list[str]:
    """Extract source experience IDs from experience_run steps."""
    return [
        step.params_template.get("source_experience_id")
        for step in experience.steps
        if step.step_type == "experience_run"
        and step.params_template
        and step.params_template.get("source_experience_id")
    ]


class ExperiencesSchedulerService:
    """Service for scheduling and executing due experiences."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings_instance()

    async def get_user_timezone(self, user_id: str) -> str | None:
        """Get user's timezone preference from UserPreferences.

        Returns None if user has no preferences (will fall back to UTC).
        """
        try:
            stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
            result = await self.db.execute(stmt)
            prefs = result.scalar_one_or_none()
            return prefs.timezone if prefs else None
        except Exception:
            return None

    async def get_all_active_users(self) -> list[User]:
        """Get all active users for experience execution.

        TODO: In the future, consider:
        - Filtering by users who have "subscribed" to experiences
        - Supporting delegation (admin runs on behalf of users)
        - Checking user preferences for experience notifications
        """
        stmt = select(User).where(User.is_active == True)  # noqa: E712
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_due_experiences(self, *, limit: int = 10) -> list[Experience]:
        """Find experiences that are due to run.

        Returns experiences where:
        - trigger_type is 'scheduled' or 'cron' (not manual)
        - next_run_at <= now
        - visibility is 'published' or 'admin_only' (not draft)
        """
        now = datetime.now(UTC)

        stmt = (
            select(Experience)
            .options(selectinload(Experience.steps))
            .where(
                and_(
                    Experience.trigger_type.in_(["scheduled", "cron", "on_linked_experiences_complete"]),
                    Experience.visibility.in_(["published", "admin_only"]),
                    Experience.next_run_at <= now,  # Due now
                )
            )
            .with_for_update(skip_locked=True)  # Avoid duplicates across workers
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def execute_experience(
        self,
        experience: Experience,
        user_id: str,
    ) -> dict[str, Any]:
        """Execute a single experience in non-streaming mode.

        Returns dict with execution status and run_id.
        """
        # Get the user who will run this experience
        user_result = await self.db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return {"status": "failed", "error": "user_not_found"}

        try:
            from shu.core.config import get_config_manager
            from shu.services.experience_executor import ExperienceExecutor

            config_manager = get_config_manager()
            executor = ExperienceExecutor(self.db, config_manager)
            run = await executor.execute(
                experience=experience,
                user_id=user_id,
                input_params={},  # No input params for scheduled runs
                current_user=user,
            )
            return {
                "status": "completed" if run.status == "succeeded" else "failed",
                "run_id": run.id,
                "error": run.error_message,
            }
        except Exception as e:
            logger.exception(
                "Scheduled experience execution failed | experience=%s user=%s",
                experience.id,
                user_id,
            )
            return {"status": "failed", "error": str(e)}

    async def _check_dependencies_resolved(self, experience: Experience) -> bool:
        """Check whether all linked experiences have completed their current cycle.

        Returns True if every linked experience has last_run_at >= this experience's
        next_run_at. Missing/deleted dependencies are treated as resolved (with warning).
        """
        linked_ids = _get_linked_experience_ids(experience)
        if not linked_ids:
            return True

        result = await self.db.execute(
            select(Experience.id, Experience.last_run_at).where(Experience.id.in_(linked_ids))
        )
        deps = {row.id: row.last_run_at for row in result.all()}

        for dep_id in linked_ids:
            if dep_id not in deps:
                logger.warning("Linked experience '%s' not found, treating as resolved", dep_id)
                continue
            last_run = deps[dep_id]
            if last_run is None or (experience.next_run_at and last_run < experience.next_run_at):
                return False

        return True

    async def _resolve_linked_experience_schedule(self, experience: Experience) -> None:
        """Set next_run_at to the MAX of linked experiences' next_run_at values.

        Excludes None values (manual triggers) and missing/deleted experiences.
        Sets next_run_at to None if all linked are manual or none exist.
        """
        linked_ids = _get_linked_experience_ids(experience)
        if not linked_ids:
            experience.next_run_at = None
            return

        result = await self.db.execute(
            select(Experience.id, Experience.next_run_at).where(Experience.id.in_(linked_ids))
        )
        next_run_values = [row.next_run_at for row in result.all() if row.next_run_at is not None]

        experience.next_run_at = max(next_run_values) if next_run_values else None

    async def _execute_and_track(self, experience: Experience, user_id: str) -> bool:
        """Execute an experience for a user and return True if succeeded.

        Checks PBAC before execution — returns False if the user is denied.
        """
        if not await POLICY_CACHE.check(str(user_id), "experience.run", f"experience:{experience.slug}", self.db):
            logger.debug("User denied experience.run | experience=%s user=%s", experience.id, user_id)
            return False
        result = await self.execute_experience(experience, user_id)
        if result.get("status") == "completed":
            logger.debug(
                "Experience completed for user | experience=%s user=%s run_id=%s",
                experience.id,
                user_id,
                result.get("run_id"),
            )
            return True
        logger.debug(
            "Experience failed for user (silent) | experience=%s user=%s error=%s",
            experience.id,
            user_id,
            result.get("error"),
        )
        return False

    async def run_due_experiences(self, *, limit: int = 10) -> dict[str, int]:
        """Find and execute due experiences for ALL active users.

        For each due experience, executes for all active users. Failures for
        individual users are logged but don't stop execution for other users.

        TODO: In the future, consider:
        - User-level opt-in/opt-out for experiences
        - Delegation mode (admin identity used for all users)
        - Pre-checking required provider connections before execution

        Returns: {"due": n, "user_runs": m, "user_failures": f, "no_users": s}
        """
        due_experiences = await self.get_due_experiences(limit=limit)

        if not due_experiences:
            return {"due": 0, "user_runs": 0, "user_failures": 0, "no_users": 0}

        # Get all active users once
        all_users = await self.get_all_active_users()
        if not all_users:
            # No users in system - still advance schedules
            for exp in due_experiences:
                exp.schedule_next()
            await self.db.commit()
            return {"due": len(due_experiences), "user_runs": 0, "user_failures": 0, "no_users": 1}

        now = datetime.now(UTC)
        user_runs = 0
        user_failures = 0

        for exp in due_experiences:
            # Dependency gate: skip if linked experiences haven't completed their cycle
            if exp.trigger_type == "on_linked_experiences_complete" and not await self._check_dependencies_resolved(
                exp
            ):
                logger.debug(
                    "Dependencies not resolved, skipping | experience=%s",
                    exp.id,
                )
                continue

            logger.info(
                "Executing scheduled experience for all users | experience=%s name=%s trigger=%s user_count=%d",
                exp.id,
                exp.name,
                exp.trigger_type,
                len(all_users),
            )

            # Per-experience counters
            run_count = 0
            failure_count = 0

            if exp.scope == "shared":
                # Shared-scope: execute once using the experience creator
                if await self._execute_and_track(exp, exp.created_by):
                    run_count = 1
                else:
                    failure_count = 1
            else:
                # Per-user scope: execute for each active user
                for user in all_users:
                    try:
                        if await self._execute_and_track(exp, user.id):
                            run_count += 1
                        else:
                            failure_count += 1
                    except Exception as e:
                        failure_count += 1
                        logger.debug(
                            "Experience execution error for user (silent) | experience=%s user=%s error=%s",
                            exp.id,
                            user.id,
                            str(e),
                        )

            # Update schedule ONCE per experience (not per user)
            exp.last_run_at = now
            if exp.trigger_type == "on_linked_experiences_complete":
                await self._resolve_linked_experience_schedule(exp)
            else:
                creator_tz = await self.get_user_timezone(exp.created_by) if exp.created_by else None
                exp.schedule_next(user_timezone=creator_tz)
            await self.db.commit()

            logger.info(
                "Experience batch complete | experience=%s runs=%d failures=%d next_run=%s",
                exp.id,
                run_count,
                failure_count,
                exp.next_run_at,
            )

            # Accumulate into global totals after logging per-experience counts
            user_runs += run_count
            user_failures += failure_count

        return {
            "due": len(due_experiences),
            "user_runs": user_runs,
            "user_failures": user_failures,
            "no_users": 0,
        }


async def start_experiences_scheduler():
    """Start the experiences scheduler background task.

    Runs in an infinite loop, checking for due experiences every tick interval.
    Designed to be started in FastAPI lifespan.
    """
    import os

    settings = get_settings_instance()

    # Check if scheduler is enabled (default: True)
    enabled_env = os.getenv("SHU_EXPERIENCES_SCHEDULER_ENABLED")
    if enabled_env is not None:
        enabled = enabled_env.lower() in ("1", "true", "yes", "on")
    else:
        enabled = getattr(settings, "experiences_scheduler_enabled", True)

    if not enabled:
        logger.info("Experiences scheduler disabled by configuration")
        return asyncio.create_task(asyncio.sleep(0), name="experiences:scheduler:disabled")

    # Get tick interval (default: 60 seconds)
    tick = max(
        10,  # Minimum 10 seconds
        int(
            os.getenv(
                "SHU_EXPERIENCES_SCHEDULER_TICK_SECONDS",
                str(getattr(settings, "experiences_scheduler_tick_seconds", 60)),
            )
        ),
    )

    # Get batch limit (default: 5)
    batch = max(
        1,
        int(
            os.getenv(
                "SHU_EXPERIENCES_SCHEDULER_BATCH_LIMIT",
                str(getattr(settings, "experiences_scheduler_batch_limit", 5)),
            )
        ),
    )

    logger.info(
        "Starting experiences scheduler | tick=%ds batch=%d",
        tick,
        batch,
    )

    async def _runner() -> None:
        while True:
            try:
                db = await get_db_session()
                async with db as session:
                    svc = ExperiencesSchedulerService(session)
                    result = await svc.run_due_experiences(limit=batch)

                    # Only log if something happened
                    if result.get("due", 0) > 0:
                        logger.info(
                            "Experiences scheduler tick | due=%d user_runs=%d user_failures=%d",
                            result.get("due", 0),
                            result.get("user_runs", 0),
                            result.get("user_failures", 0),
                        )
            except Exception as ex:
                logger.warning(f"Experiences scheduler tick failed: {ex}")
            finally:
                try:
                    await asyncio.sleep(tick)
                except asyncio.CancelledError:
                    logger.info("Experiences scheduler stopped")
                    break

    return asyncio.create_task(_runner(), name="experiences:scheduler")
