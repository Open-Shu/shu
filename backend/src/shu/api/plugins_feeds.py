"""Plugins API (admin feeds): create/list/update/delete feeds and run controls
Preserves original paths under /plugins/admin/feeds*.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.database import get_async_session_local
from ..core.response import ShuResponse
from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_feed import PluginFeed
from ..plugins.registry import REGISTRY
from ..services.plugin_identity import compute_identity_status
from ..services.plugins_feed_policy import enforce_feed_op

router = APIRouter()


class CreateScheduleRequest(BaseModel):
    name: str
    plugin_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = 3600
    agent_key: str | None = None
    enabled: bool = True
    owner_user_id: str | None = None


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    plugin_name: str | None = None
    params: dict[str, Any] | None = None
    interval_seconds: int | None = None
    agent_key: str | None = None
    enabled: bool | None = None
    next_run_at: str | None = None
    owner_user_id: str | None = None


class RunPendingRequest(BaseModel):
    limit: int = 10
    schedule_id: str | None = None
    execution_id: str | None = None


@router.post("/admin/feeds")
async def admin_create_schedule(
    body: CreateScheduleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    owner_id = str(body.owner_user_id) if body.owner_user_id else str(admin.id)

    params = enforce_feed_op(body.plugin_name, dict(body.params or {}))

    sched = PluginFeed(
        name=body.name,
        plugin_name=body.plugin_name,
        params=params,
        interval_seconds=max(1, int(body.interval_seconds or 3600)),
        agent_key=body.agent_key,
        owner_user_id=owner_id,
        enabled=bool(body.enabled),
        next_run_at=datetime.now(UTC),
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return ShuResponse.success(sched.to_dict())


@router.get("/admin/feeds")
async def admin_list_schedules(
    plugin_name: str | None = Query(None, description="Filter by plugin name"),
    owner_user_id: str | None = Query(None, description="Filter by owner user id"),
    kb_id: str | None = Query(None, description="Filter by params.kb_id"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginFeed))
    scheds = res.scalars().all()
    if plugin_name:
        scheds = [s for s in scheds if s.plugin_name == plugin_name]
    if owner_user_id:
        scheds = [s for s in scheds if (s.owner_user_id or "") == owner_user_id]
    if kb_id:

        def _get_kb(p):
            try:
                return (p or {}).get("kb_id")
            except Exception:
                return None

        scheds = [s for s in scheds if _get_kb(s.params) == kb_id]

    out = []
    for s in scheds:
        row = s.to_dict()
        row["identity_status"] = await compute_identity_status(db, s.owner_user_id, s.params or {})
        out.append(row)

    return ShuResponse.success(sorted(out, key=lambda x: (not x.get("enabled", False), x.get("name"))))


@router.get("/admin/feeds/{schedule_id}")
async def admin_get_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    """Get a single feed by ID."""
    res = await db.execute(select(PluginFeed).where(PluginFeed.id == schedule_id))
    sched = res.scalars().first()
    if not sched:
        raise HTTPException(status_code=404, detail="schedule not found")
    row = sched.to_dict()
    row["identity_status"] = await compute_identity_status(db, sched.owner_user_id, sched.params or {})
    return ShuResponse.success(row)


@router.patch("/admin/feeds/{schedule_id}")
async def admin_update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginFeed).where(PluginFeed.id == schedule_id))
    sched = res.scalars().first()
    if not sched:
        raise HTTPException(status_code=404, detail="schedule not found")

    if body.plugin_name is not None and body.plugin_name != sched.plugin_name:
        plugin = await REGISTRY.resolve(body.plugin_name, db)
        if not plugin:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "plugin_not_found",
                    "message": f"Plugin '{body.plugin_name}' not found or disabled",
                },
            )
        sched.plugin_name = body.plugin_name

    if body.name is not None:
        sched.name = body.name

    if body.params is not None:
        new_params = enforce_feed_op(sched.plugin_name, dict(body.params or {}))
        sched.params = new_params

    if body.interval_seconds is not None:
        sched.interval_seconds = max(1, int(body.interval_seconds))

    if body.agent_key is not None:
        sched.agent_key = body.agent_key

    if body.enabled is not None:
        sched.enabled = bool(body.enabled)
        if not sched.enabled:
            now = datetime.now(UTC)
            await db.execute(
                update(PluginExecution)
                .where(
                    (PluginExecution.schedule_id == schedule_id)
                    & (PluginExecution.status == PluginExecutionStatus.PENDING)
                )
                .values(
                    status=PluginExecutionStatus.FAILED,
                    error="cancelled_disabled",
                    completed_at=now,
                )
            )

    if body.owner_user_id is not None:
        sched.owner_user_id = str(body.owner_user_id) if body.owner_user_id else None

    if body.next_run_at:
        try:
            sched.next_run_at = datetime.fromisoformat(body.next_run_at)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid next_run_at format")

    await db.commit()
    await db.refresh(sched)
    return ShuResponse.success(sched.to_dict())


@router.delete("/admin/feeds/{schedule_id}")
async def admin_delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginFeed).where(PluginFeed.id == schedule_id))
    sched = res.scalars().first()
    if not sched:
        raise HTTPException(status_code=404, detail="schedule not found")
    from sqlalchemy import delete

    await db.execute(delete(PluginExecution).where(PluginExecution.schedule_id == schedule_id))
    await db.delete(sched)
    await db.commit()
    return ShuResponse.success({"status": "deleted", "id": schedule_id})


@router.post("/admin/feeds/run-due")
async def admin_enqueue_due_schedules(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    from ..services.plugins_scheduler_service import PluginsSchedulerService

    svc = PluginsSchedulerService(db)
    stats = await svc.enqueue_due_schedules(fallback_user_id=str(admin.id))
    return ShuResponse.success(stats)


@router.post("/admin/feeds/{schedule_id}/run-now")
async def admin_run_schedule_now(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginFeed).where(PluginFeed.id == schedule_id))
    sched = res.scalars().first()
    if not sched:
        raise HTTPException(status_code=404, detail="schedule not found")
    if not sched.enabled:
        raise HTTPException(status_code=400, detail="schedule is disabled")

    # Enqueue one execution immediately
    exec_rec = PluginExecution(
        schedule_id=sched.id,
        plugin_name=sched.plugin_name,
        user_id=str(sched.owner_user_id or admin.id),
        agent_key=sched.agent_key,
        params=sched.params or {},
        status=PluginExecutionStatus.PENDING,
    )
    db.add(exec_rec)
    # Advance schedule window so we don't re-enqueue on next tick
    sched.schedule_next()
    await db.commit()

    # Capture execution ID before spawning background task
    exec_id = str(exec_rec.id)

    # Fire-and-forget: spawn background task to run immediately
    # Don't await - return to frontend immediately so it can poll for status
    async def _run_in_background() -> None:
        try:
            async with get_async_session_local()() as bg_session:
                from ..services.plugins_scheduler_service import PluginsSchedulerService

                svc = PluginsSchedulerService(bg_session)
                await svc.run_pending(limit=1, execution_id=exec_id)
        except Exception:
            # Best-effort immediate run; if it fails, scheduler will pick it up
            pass

    asyncio.create_task(_run_in_background())  # noqa: RUF006 # We run this in the background, currently no way to store

    # Return the execution record immediately (status will be PENDING)
    await db.refresh(exec_rec)
    return ShuResponse.success(exec_rec.to_dict())
