"""In-process Plugin Feeds Scheduler service.

- Enqueue due PluginFeed rows into QueueBackend as jobs with MAINTENANCE WorkloadType
- Workers dequeue and process jobs from the queue
- PluginExecution records track execution state for idempotency and observability
- Exposed start_plugins_scheduler() to run in FastAPI lifespan

DRY: API endpoints delegate to this service.
Concurrency: uses QueueBackend for job distribution and SELECT ... FOR UPDATE SKIP LOCKED
for database-level idempotency guards.

Migration Note (SHU-211): Migrated from direct execution to queue-based job distribution.
Jobs are now enqueued to QueueBackend with MAINTENANCE WorkloadType and processed by workers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..core.database import get_db_session
from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_feed import PluginFeed
from ..models.plugin_registry import PluginDefinition
from ..models.provider_identity import ProviderIdentity
from ..plugins.executor import EXECUTOR
from ..plugins.host.auth_capability import AuthCapability
from ..plugins.registry import REGISTRY
from ..services.plugin_identity import (
    PluginIdentityError,
    ensure_secrets_for_plugin,
    resolve_auth_requirements,
)

# In-memory per-process tick history for observability (SCHED-004)
# Stores last 500 tick summaries: {"ts": iso8601, "enqueue": {...}, "run": {...}}
TICK_HISTORY = deque(maxlen=500)

# Feed params that should be automatically cleared after successful execution.
# These are "one-shot" params meant to apply only once.
ONE_SHOT_FEED_PARAMS = ("reset_cursor",)

logger = logging.getLogger(__name__)


class PluginsSchedulerService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings_instance()

    async def enqueue_due_schedules(self, *, limit: Optional[int] = None, fallback_user_id: Optional[str] = None) -> Dict[str, int]:
        """Atomically claim due schedules and enqueue executions to QueueBackend.
        
        Creates PluginExecution records for tracking and idempotency, then enqueues
        jobs to QueueBackend with MAINTENANCE WorkloadType for worker processing.
        
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
                    or_(PluginFeed.next_run_at == None, PluginFeed.next_run_at <= now),
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
            
            # Enqueue job to QueueBackend with MAINTENANCE WorkloadType
            if queue_backend:
                try:
                    job = await enqueue_job(
                        queue_backend,
                        WorkloadType.MAINTENANCE,
                        payload={
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
                        }
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to enqueue job to queue: {e}",
                        extra={"execution_id": exec_rec.id, "schedule_id": s.id}
                    )
                    # Continue - the PluginExecution record is created, so run_pending can still process it
            
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

    async def _clear_one_shot_feed_params(self, feed_id: str) -> None:
        """Clear one-shot params from a feed after successful execution.

        Uses ONE_SHOT_FEED_PARAMS module constant to determine which params to clear.
        These params are meant to apply only once and should be cleared automatically
        to prevent repeated application on subsequent runs.
        """
        try:
            res = await self.db.execute(select(PluginFeed).where(PluginFeed.id == feed_id))
            feed = res.scalars().first()
            if not feed or not feed.params:
                return
            params = dict(feed.params) if isinstance(feed.params, dict) else {}
            modified = False
            for key in ONE_SHOT_FEED_PARAMS:
                if key in params:
                    del params[key]
                    modified = True
            if modified:
                feed.params = params
        except Exception:
            # Best-effort cleanup; don't fail execution if this fails
            pass

    async def run_pending(
        self, *, limit: int = 10, schedule_id: str | None = None, execution_id: str | None = None
    ) -> dict[str, int]:
        """Claim up to limit pending executions and run them sequentially.
        Returns: {"attempted": n, "ran": m, "failed_owner_required": x, "skipped_disabled": y}
        """
        # Cleanup: mark stale RUNNING executions as failed to unblock schedules
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
                        & (PluginExecution.started_at <= cutoff)
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

        claimed = await self._claim_pending(
            limit=max(1, int(limit)), schedule_id=schedule_id, execution_id=execution_id
        )
        ran = 0
        failed_owner_required = 0
        skipped_disabled = 0
        deferred_429 = 0

        async def _fail(rec: PluginExecution, error_code: str) -> None:
            rec.status = PluginExecutionStatus.FAILED
            rec.error = error_code
            rec.result = {"status": "error", "error": error_code}
            rec.completed_at = datetime.now(UTC)
            await self.db.commit()

        for rec in claimed:
            try:
                # If schedule is disabled, skip running and mark as cancelled
                if rec.schedule_id:
                    srow = await self.db.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
                    s = srow.scalars().first()
                    if s and not s.enabled:
                        rec.status = PluginExecutionStatus.FAILED
                        rec.error = "schedule_disabled"
                        rec.completed_at = datetime.now(UTC)
                        await self.db.commit()
                        skipped_disabled += 1
                        continue

                # Resolve plugin
                plugin = await REGISTRY.resolve(rec.plugin_name, self.db)
                if not plugin:
                    # Mark failed and auto-disable the feed to prevent repeated failures
                    rec.status = PluginExecutionStatus.FAILED
                    rec.error = "plugin_not_found"
                    rec.completed_at = datetime.now(UTC)
                    if rec.schedule_id:
                        try:
                            srow = await self.db.execute(select(PluginFeed).where(PluginFeed.id == rec.schedule_id))
                            s = srow.scalars().first()
                            if s and s.enabled:
                                s.enabled = False
                        except Exception:
                            pass
                    await self.db.commit()
                    continue

                # Per-plugin limits
                lrow = await self.db.execute(select(PluginDefinition).where(PluginDefinition.name == rec.plugin_name))
                ldef = lrow.scalars().first()
                per_plugin_limits = getattr(ldef, "limits", None) or {}

                # Identity resolution and enforcement for scheduled execution
                p = rec.params or {}
                mode = str(p.get("auth_mode") or "").lower()
                user_email_val = p.get("user_email")
                if not user_email_val and mode == "domain_delegate":
                    imp = p.get("impersonate_email")
                    if imp:
                        user_email_val = imp

                # Build provider identities map for the owner/runner
                providers_map: dict[str, list[dict[str, Any]]] = {}
                try:
                    q_pi = select(ProviderIdentity).where(ProviderIdentity.user_id == str(rec.user_id))
                    pi_res = await self.db.execute(q_pi)
                    for pi in pi_res.scalars().all():
                        providers_map.setdefault(pi.provider_key, []).append(pi.to_dict())
                except Exception:
                    providers_map = {}

                # Provider-agnostic identity preflight using AuthCapability (no provider_identity_preflight method in modular API)
                try:
                    provider, mode_eff, subject, scopes = resolve_auth_requirements(plugin, rec.params or {})
                    if provider:
                        auth = AuthCapability(plugin_name=str(rec.plugin_name), user_id=str(rec.user_id))
                        mode_str = (mode_eff or "").strip().lower()
                        sc = scopes or []
                        if mode_str == "user":
                            # Execution-time subscription enforcement (TASK-163)
                            try:
                                from ..services.host_auth_service import HostAuthService

                                subs = await HostAuthService.list_subscriptions(
                                    self.db, str(rec.user_id), provider, None
                                )
                                if subs:
                                    subscribed_names = {s.plugin_name for s in subs}
                                    if str(rec.plugin_name) not in subscribed_names:
                                        try:
                                            logger.warning(
                                                "subscription.enforced | user=%s provider=%s plugin=%s path=scheduler",
                                                str(rec.user_id),
                                                provider,
                                                str(rec.plugin_name),
                                            )
                                        except Exception:
                                            pass
                                        await _fail(rec, "subscription_required")
                                        continue
                            except Exception:
                                # Do not block execution if enforcement check fails unexpectedly
                                pass
                            tok = await auth.provider_user_token(provider, required_scopes=sc or None)
                            if not tok:
                                await _fail(rec, "identity_required")
                                continue
                        elif mode_str == "domain_delegate":
                            subj = (subject or "").strip()
                            if not subj:
                                await _fail(rec, "identity_required")
                                continue
                            resp = await auth.provider_delegation_check(provider, scopes=sc, subject=subj)
                            if not (isinstance(resp, dict) and resp.get("ready") is True):
                                await _fail(rec, "identity_required")
                                continue
                        elif mode_str == "service_account":
                            try:
                                _ = await auth.provider_service_account_token(provider, scopes=sc, subject=None)
                            except Exception:
                                await _fail(rec, "identity_required")
                                continue
                except Exception:
                    # Any failure to resolve requirements or perform preflight defaults to allow
                    pass

                # Secrets preflight: ensure declared secrets are available for the op
                try:
                    await ensure_secrets_for_plugin(plugin, str(rec.plugin_name), str(rec.user_id), rec.params or {})
                except PluginIdentityError:
                    await _fail(rec, "missing_secrets")
                    continue
                except Exception as e:
                    # Non-blocking on unexpected errors, but log for debugging
                    logger.warning(
                        "Secrets preflight check failed unexpectedly for feed %s plugin %s: %s",
                        rec.id,
                        rec.plugin_name,
                        e,
                    )

                # Inject schedule_id into params so plugins can scope cursors per-feed
                base_params = rec.params or {}
                eff_params = dict(base_params) if isinstance(base_params, dict) else {}
                if rec.schedule_id:
                    eff_params["__schedule_id"] = str(rec.schedule_id)
                result = await EXECUTOR.execute(
                    plugin=plugin,
                    user_id=str(rec.user_id),
                    user_email=user_email_val,
                    agent_key=rec.agent_key,
                    params=eff_params,
                    limits=per_plugin_limits,
                    provider_identities=providers_map,
                )
                try:
                    payload = result.model_dump()
                except Exception:
                    if isinstance(result, dict):
                        payload = result
                    else:
                        payload = {
                            "status": getattr(result, "status", None),
                            "data": getattr(result, "data", None),
                            "error": getattr(result, "error", None),
                        }

                # Enforce output byte cap if configured
                try:
                    payload_json = json.dumps(payload, separators=(",", ":"), default=str)
                    payload_size = len(payload_json.encode("utf-8"))
                except Exception:
                    payload_size = 0
                if (
                    getattr(self.settings, "plugin_exec_output_max_bytes", 0)
                    and self.settings.plugin_exec_output_max_bytes > 0
                ):
                    if payload_size > self.settings.plugin_exec_output_max_bytes:
                        rec.completed_at = datetime.now(UTC)
                        rec.status = PluginExecutionStatus.FAILED
                        rec.error = (
                            f"output exceeds max bytes ({payload_size} > {self.settings.plugin_exec_output_max_bytes})"
                        )
                        rec.result = {"status": "error", "error": "output_too_large"}
                        await self.db.commit()
                        continue

                rec.result = payload
                rec.completed_at = datetime.now(UTC)
                rec.status = (
                    PluginExecutionStatus.COMPLETED
                    if payload.get("status") == "success"
                    else PluginExecutionStatus.FAILED
                )
                _err_val = payload.get("error") if payload.get("status") != "success" else None
                if isinstance(_err_val, (dict, list)):
                    rec.error = json.dumps(_err_val, separators=(",", ":"), default=str)
                else:
                    rec.error = _err_val

                # Unified diagnostics logging (DRY)
                try:
                    from ..plugins.utils import log_plugin_diagnostics as _log_diags
                except Exception:
                    _log_diags = None
                if _log_diags:
                    _log_diags(payload, plugin_name=str(rec.plugin_name), exec_id=str(rec.id))

                # Clear one-shot params (reset_cursor) from feed after successful execution
                if rec.status == PluginExecutionStatus.COMPLETED and rec.schedule_id:
                    await self._clear_one_shot_feed_params(rec.schedule_id)

                await self.db.commit()
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
                        # Choose a backoff delay
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
            "stale_cleaned": stale_cleaned,
            "deferred_429": deferred_429,
        }


async def start_plugins_scheduler():
    import os

    settings = get_settings_instance()
    # Read runtime env to allow tests to override after app import
    enabled_env = os.getenv("SHU_PLUGINS_SCHEDULER_ENABLED")
    if enabled_env is not None:
        enabled = enabled_env.lower() in ("1", "true", "yes", "on")
    else:
        enabled = getattr(settings, "plugins_scheduler_enabled", True)
    if not enabled:
        logger.info("Plugins scheduler disabled by configuration")
        return asyncio.create_task(asyncio.sleep(0), name="plugins:scheduler:disabled")

    tick = max(
        1,
        int(
            os.getenv(
                "SHU_PLUGINS_SCHEDULER_TICK_SECONDS",
                str(getattr(settings, "plugins_scheduler_tick_seconds", 60)),
            )
        ),
    )
    batch = max(
        1,
        int(
            os.getenv(
                "SHU_PLUGINS_SCHEDULER_BATCH_LIMIT",
                str(getattr(settings, "plugins_scheduler_batch_limit", 10)),
            )
        ),
    )

    async def _runner():
        while True:
            try:
                db = await get_db_session()
                async with db as session:
                    svc = PluginsSchedulerService(session)
                    e = await svc.enqueue_due_schedules(limit=batch)
                    r = await svc.run_pending(limit=batch)
                    if (e.get("enqueued") or 0) or (r.get("ran") or 0):
                        logger.info(
                            "Plugins scheduler tick | due=%s enq=%s queue_enq=%s skipped_no_owner=%s skipped_missing_plugin=%s skipped_already_enqueued=%s attempted=%s ran=%s failed_owner_required=%s skipped_disabled=%s stale_cleaned=%s deferred_429=%s",
                            e.get("due"),
                            e.get("enqueued"),
                            e.get("queue_enqueued"),
                            e.get("skipped_no_owner"),
                            e.get("skipped_missing_plugin"),
                            e.get("skipped_already_enqueued"),
                            r.get("attempted"),
                            r.get("ran"),
                            r.get("failed_owner_required"),
                            r.get("skipped_disabled"),
                            r.get("stale_cleaned"),
                            r.get("deferred_429"),
                        )
                        try:
                            TICK_HISTORY.append(
                                {
                                    "ts": datetime.now(UTC).isoformat(),
                                    "enqueue": e,
                                    "run": r,
                                }
                            )
                        except Exception:
                            pass
            except Exception as ex:
                logger.warning(f"Plugins scheduler tick failed: {ex}")
            finally:
                try:
                    await asyncio.sleep(tick)
                except asyncio.CancelledError:
                    break

    return asyncio.create_task(_runner(), name="plugins:scheduler")
