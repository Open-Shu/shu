"""
Plugins API (admin executions): list/get/run-pending + scheduler metrics
Preserves original paths under /plugins/admin/executions* and /plugins/admin/scheduler/metrics
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..api.dependencies import get_db
from ..auth.rbac import require_power_user
from ..auth.models import User
from ..core.response import ShuResponse
from ..models.plugin_execution import PluginExecution

router = APIRouter()


class RunPendingRequest(BaseModel):
    limit: int = 10
    schedule_id: Optional[str] = None
    execution_id: Optional[str] = None


@router.get("/admin/executions")
async def admin_list_executions(
    schedule_id: Optional[str] = Query(None, description="Filter by schedule id"),
    plugin_name: Optional[str] = Query(None, description="Filter by plugin name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, description="Max rows to return (default 50)"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(
        select(PluginExecution)
            .order_by(PluginExecution.created_at.desc(), PluginExecution.started_at.desc(), PluginExecution.id.desc())
    )
    execs = res.scalars().all()
    if schedule_id:
        execs = [e for e in execs if (e.schedule_id or "") == schedule_id]
    if plugin_name:
        execs = [e for e in execs if (e.plugin_name or "") == plugin_name]
    if status:
        execs = [e for e in execs if (e.status or "") == status]

    rows = [e.to_dict() for e in execs[: max(1, int(limit))]]
    for r in rows:
        if r.get("params") is None:
            r["params"] = {}
        if r.get("result") is None:
            r["result"] = {}
    return ShuResponse.success(rows)


@router.get("/admin/executions/{execution_id}")
async def admin_get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginExecution).where(PluginExecution.id == execution_id))
    rec = res.scalars().first()
    if not rec:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="execution not found")
    return ShuResponse.success(rec.to_dict())


@router.post("/admin/executions/run-pending")
async def admin_run_pending(
    body: RunPendingRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    from ..services.plugins_scheduler_service import PluginsSchedulerService
    svc = PluginsSchedulerService(db)
    stats = await svc.run_pending(
        limit=max(1, int(getattr(body, "limit", 10) or 10)),
        schedule_id=getattr(body, "schedule_id", None),
        execution_id=getattr(body, "execution_id", None),
    )
    return ShuResponse.success(stats)


@router.get("/admin/scheduler/metrics")
async def admin_scheduler_metrics(
    limit: int = Query(50, description="Number of recent tick summaries to return (default 50)"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    from ..services.plugins_scheduler_service import TICK_HISTORY
    try:
        lim = max(1, min(int(limit), 500))
    except Exception:
        lim = 50
    items = list(TICK_HISTORY)
    rows = items[-lim:][::-1]
    return ShuResponse.success(rows)
