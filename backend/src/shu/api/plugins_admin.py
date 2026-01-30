"""Plugins API (admin): registry controls, limits, upload, delete, sync
Preserves original paths under /plugins/admin and /plugins/upload
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.config import get_settings_instance
from ..core.response import ShuResponse
from ..models.plugin_registry import PluginDefinition
from ..plugins.installer import InstallError, install_plugin, validate_and_extract
from ..plugins.registry import REGISTRY
from ..schemas.envelope import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_plugins_root(settings) -> Path:
    """Resolve plugins_root relative to the repository root when configured as a relative path."""
    plugins_root = Path(settings.plugins_root)
    if plugins_root.is_absolute():
        return plugins_root
    repo_root = settings.__class__._repo_root_from_this_file()
    return (repo_root / plugins_root).resolve()


class PluginEnableRequest(BaseModel):
    enabled: bool


class PluginSchemaRequest(BaseModel):
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


@router.patch("/admin/{name}/enable", response_model=SuccessResponse[dict])
async def admin_set_plugin_enabled(
    name: str,
    body: PluginEnableRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    row = res.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    row.enabled = body.enabled
    await db.commit()
    await db.refresh(row)
    return ShuResponse.success(
        {
            "name": row.name,
            "version": getattr(row, "version", None),
            "enabled": bool(row.enabled),
            "input_schema": getattr(row, "input_schema", None),
            "output_schema": getattr(row, "output_schema", None),
        }
    )


@router.put("/admin/{name}/schema", response_model=SuccessResponse[dict])
async def admin_set_plugin_schema(
    name: str,
    body: PluginSchemaRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    row = res.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    if body.input_schema is not None:
        row.input_schema = body.input_schema
    if body.output_schema is not None:
        row.output_schema = body.output_schema
    await db.commit()
    await db.refresh(row)
    return ShuResponse.success(
        {
            "name": row.name,
            "version": getattr(row, "version", None),
            "enabled": bool(row.enabled),
            "input_schema": getattr(row, "input_schema", None),
            "output_schema": getattr(row, "output_schema", None),
        }
    )


class PluginLimitsRequest(BaseModel):
    rate_limit_user_requests: int | None = None
    rate_limit_user_period: int | None = None
    quota_daily_requests: int | None = None
    quota_monthly_requests: int | None = None
    provider_name: str | None = None
    provider_rpm: int | None = None
    provider_window_seconds: int | None = None
    provider_concurrency: int | None = None


@router.get("/admin/{name}/limits", response_model=SuccessResponse[dict])
async def admin_get_plugin_limits(
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    row = res.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    return ShuResponse.success({"name": name, "limits": getattr(row, "limits", {}) or {}})


@router.put("/admin/{name}/limits", response_model=SuccessResponse[dict])
async def admin_set_plugin_limits(
    name: str,
    body: PluginLimitsRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    row = res.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")
    limits = dict(getattr(row, "limits", {}) or {})
    if body.rate_limit_user_requests is not None:
        limits["rate_limit_user_requests"] = int(body.rate_limit_user_requests)
    if body.rate_limit_user_period is not None:
        limits["rate_limit_user_period"] = int(body.rate_limit_user_period)
    if body.quota_daily_requests is not None:
        limits["quota_daily_requests"] = int(body.quota_daily_requests)
    if body.quota_monthly_requests is not None:
        limits["quota_monthly_requests"] = int(body.quota_monthly_requests)
    if body.provider_name is not None:
        limits["provider_name"] = str(body.provider_name)
    if body.provider_rpm is not None:
        limits["provider_rpm"] = int(body.provider_rpm)
    if body.provider_window_seconds is not None:
        limits["provider_window_seconds"] = int(body.provider_window_seconds)
    if body.provider_concurrency is not None:
        limits["provider_concurrency"] = int(body.provider_concurrency)
    row.limits = limits
    await db.commit()
    await db.refresh(row)
    return ShuResponse.success({"name": name, "limits": row.limits or {}})


@router.post("/admin/sync", response_model=SuccessResponse[dict])
async def admin_sync_plugins(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    stats = await REGISTRY.sync(db)
    return ShuResponse.success(stats)


class PluginUploadResponse(BaseModel):
    plugin_name: str
    version: str | None = None
    installed_path: str
    warnings: list[str] = Field(default_factory=list)
    restart_required: bool = False


@router.post("/upload", response_model=SuccessResponse[PluginUploadResponse])
async def admin_upload_plugin(
    file: UploadFile = File(...),
    force: bool = Form(False),
    admin: User = Depends(require_power_user),
):
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": "read_failure", "message": str(e)})

    try:
        temp_dir, plugin_root, manifest, warnings = validate_and_extract(content)
    except InstallError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_plugin_package", "message": str(e)})

    try:
        settings = get_settings_instance()
        plugins_root = _resolve_plugins_root(settings)

        installed_path = install_plugin(plugin_root, plugins_root, force=bool(force))
        try:
            REGISTRY.refresh()
            restart_required = False
        except Exception:
            restart_required = True

        resp = PluginUploadResponse(
            plugin_name=str(manifest.get("name")),
            version=str(manifest.get("version") or ""),
            installed_path=str(installed_path),
            warnings=warnings,
            restart_required=restart_required,
        )
        return ShuResponse.success(resp)
    finally:
        try:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


@router.delete("/admin/{name}", response_model=SuccessResponse[dict])
async def admin_delete_plugin(
    name: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_power_user),
):
    """Delete plugin: guarded FS delete + referential integrity with feeds.
    Behavior: block when dependent feeds exist (409) listing feed ids.
    """
    # Referential integrity: block if feeds reference this plugin
    from ..models.plugin_feed import PluginFeed

    dep_res = await db.execute(select(PluginFeed.id).where(PluginFeed.plugin_name == name))
    dep_ids = [r[0] for r in dep_res.all()]
    if dep_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "plugin_delete_blocked",
                "message": f"Cannot delete plugin '{name}' with dependent feeds",
                "dependent_feed_ids": dep_ids,
            },
        )

    # Guarded FS removal (only under configured plugins_root)
    try:
        manifest = REGISTRY.get_manifest(refresh_if_empty=True)
        rec = manifest.get(name)
        if rec and getattr(rec, "plugin_dir", None):
            try:
                p = Path(rec.plugin_dir).resolve()
                settings = get_settings_instance()
                root = _resolve_plugins_root(settings)
                if str(p).startswith(str(root)):
                    import shutil

                    shutil.rmtree(p, ignore_errors=True)
                else:
                    logger.warning("Skip deleting '%s': outside plugins_root '%s'", p, root)
            except Exception as e:
                logger.warning("Failed to remove plugin directory for %s: %s", name, e)
    except Exception:
        pass

    # Purge DB registry rows
    try:
        res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
        rows = res.scalars().all()
        for r in rows:
            await db.delete(r)
        if rows:
            await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail={"error": "delete_failed", "message": str(e)})

    try:
        REGISTRY.refresh()
    except Exception:
        pass

    return ShuResponse.success({"status": "deleted", "name": name})
