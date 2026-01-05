"""
Experiences Scheduler Service.

In-process scheduler that executes due experiences based on their trigger_type
and trigger_config. Follows the same pattern as PluginsSchedulerService.

- Queries experiences with next_run_at <= now
- Executes them via ExperienceExecutor (non-streaming mode)
- Updates next_run_at using schedule_next() for next execution window
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.auth.models import User
from shu.core.config import get_settings_instance
from shu.core.database import get_db_session
from shu.models.experience import Experience
from shu.models.user_preferences import UserPreferences


logger = logging.getLogger(__name__)


class ExperiencesSchedulerService:
    """Service for scheduling and executing due experiences."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings_instance()

    async def get_user_timezone(self, user_id: str) -> Optional[str]:
        """
        Get user's timezone preference from UserPreferences.
        
        Returns None if user has no preferences (will fall back to UTC).
        """
        try:
            stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
            result = await self.db.execute(stmt)
            prefs = result.scalar_one_or_none()
            return prefs.timezone if prefs else None
        except Exception:
            return None

    async def get_due_experiences(self, *, limit: int = 10) -> List[Experience]:
        """
        Find experiences that are due to run.
        
        Returns experiences where:
        - trigger_type is 'scheduled' or 'cron' (not manual)
        - next_run_at <= now
        - visibility is 'published' or 'admin_only' (not draft)
        """
        now = datetime.now(timezone.utc)
        
        stmt = (
            select(Experience)
            .options(selectinload(Experience.steps))
            .where(
                and_(
                    Experience.trigger_type.in_(["scheduled", "cron"]),
                    Experience.visibility.in_(["published", "admin_only"]),
                    or_(
                        Experience.next_run_at == None,  # noqa: E711 - Never scheduled
                        Experience.next_run_at <= now,   # Due now
                    ),
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
    ) -> Dict[str, Any]:
        """
        Execute a single experience in non-streaming mode.
        
        Returns dict with execution status and run_id.
        """
        from .experience_executor import ExperienceExecutor
        
        # Get the user who will run this experience
        user_result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return {"status": "failed", "error": "user_not_found"}
        
        try:
            from shu.services.experience_executor import ExperienceExecutor
            from shu.core.config import get_config_manager
            
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

    async def run_due_experiences(self, *, limit: int = 10) -> Dict[str, int]:
        """
        Find and execute due experiences.
        
        Returns: {"due": n, "ran": m, "failed": f, "skipped_no_owner": s}
        """
        due_experiences = await self.get_due_experiences(limit=limit)
        
        ran = 0
        failed = 0
        skipped_no_owner = 0
        
        now = datetime.now(timezone.utc)

        logger.info("NOW: %s", now)
        
        for exp in due_experiences:

            # TODO: We need to trigger this for other users as well.

            # Use experience creator as the runner for scheduled executions
            runner_user_id = exp.created_by
            if not runner_user_id:
                skipped_no_owner += 1
                # Still advance the schedule to avoid tight loops
                exp.schedule_next()
                await self.db.commit()
                continue
            
            # Get user's timezone preference for scheduling
            user_tz = await self.get_user_timezone(runner_user_id)
            
            logger.info(
                "Executing scheduled experience | experience=%s name=%s trigger=%s user=%s timezone=%s",
                exp.id,
                exp.name,
                exp.trigger_type,
                runner_user_id,
                user_tz or "UTC",
            )
            
            try:
                result = await self.execute_experience(exp, runner_user_id)
                
                if result.get("status") == "completed":
                    ran += 1
                else:
                    failed += 1
                    logger.warning(
                        "Scheduled experience failed | experience=%s error=%s",
                        exp.id,
                        result.get("error"),
                    )
                
                # Update last_run_at and advance schedule with user's timezone
                exp.last_run_at = now
                exp.schedule_next(user_timezone=user_tz)
                await self.db.commit()
                
            except Exception as e:
                failed += 1
                logger.exception(
                    "Unexpected error executing scheduled experience | experience=%s",
                    exp.id,
                )
                # Still advance schedule to prevent tight loops
                exp.schedule_next(user_timezone=user_tz)
                await self.db.commit()
        
        return {
            "due": len(due_experiences),
            "ran": ran,
            "failed": failed,
            "skipped_no_owner": skipped_no_owner,
        }


async def start_experiences_scheduler():
    """
    Start the experiences scheduler background task.
    
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
        int(os.getenv(
            "SHU_EXPERIENCES_SCHEDULER_TICK_SECONDS",
            str(getattr(settings, "experiences_scheduler_tick_seconds", 60))
        ))
    )
    
    # Get batch limit (default: 5)
    batch = max(
        1,
        int(os.getenv(
            "SHU_EXPERIENCES_SCHEDULER_BATCH_LIMIT",
            str(getattr(settings, "experiences_scheduler_batch_limit", 5))
        ))
    )
    
    logger.info(
        "Starting experiences scheduler | tick=%ds batch=%d",
        tick,
        batch,
    )
    
    async def _runner():
        while True:
            try:
                db = await get_db_session()
                async with db as session:
                    svc = ExperiencesSchedulerService(session)
                    result = await svc.run_due_experiences(limit=batch)
                    
                    # Only log if something happened
                    if result.get("due", 0) > 0:
                        logger.info(
                            "Experiences scheduler tick | due=%d ran=%d failed=%d skipped_no_owner=%d",
                            result.get("due", 0),
                            result.get("ran", 0),
                            result.get("failed", 0),
                            result.get("skipped_no_owner", 0),
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
