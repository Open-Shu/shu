"""Plugins API (public): list/get/execute
Preserves original paths under /plugins.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.config import get_settings_instance
from ..core.response import ShuResponse
from ..models.plugin_execution import PluginExecution, PluginExecutionStatus
from ..models.plugin_registry import PluginDefinition
from ..plugins.executor import EXECUTOR
from ..plugins.registry import REGISTRY
from ..schemas.envelope import SuccessResponse
from ..services.plugin_identity import get_provider_identities_map, resolve_user_email_for_execution
from ..services.plugin_validation import enforce_input_limit, enforce_output_limit

logger = get_logger(__name__)

router = APIRouter()  # included under /plugins by parent aggregator


class PluginExecuteRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    agent_key: str | None = None


class PluginInfoResponse(BaseModel):
    name: str
    version: str | None = None
    enabled: bool
    display_name: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    capabilities: list[str] | None = None
    required_identities: list[dict[str, Any]] | None = None
    op_auth: dict[str, Any] | None = None
    default_feed_op: str | None = None
    allowed_feed_ops: list[str] | None = None


@router.get("/", response_model=SuccessResponse[list[PluginInfoResponse]])
async def list_plugins(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(select(PluginDefinition))
    rows = res.scalars().all()

    manifest = REGISTRY.get_manifest(refresh_if_empty=True)

    db_by_name = {r.name: r for r in rows}
    out: list[PluginInfoResponse] = []
    for name, rec in (manifest or {}).items():
        try:
            r = db_by_name.get(name)
            caps = list(rec.capabilities or []) if getattr(rec, "capabilities", None) is not None else None
            req_ids = (
                list(rec.required_identities or []) if getattr(rec, "required_identities", None) is not None else None
            )
            op_auth = dict(rec.op_auth or {}) if getattr(rec, "op_auth", None) is not None else None
            default_feed_op = rec.default_feed_op if getattr(rec, "default_feed_op", None) is not None else None
            allowed_feed_ops = (
                list(rec.allowed_feed_ops or []) if getattr(rec, "allowed_feed_ops", None) is not None else None
            )
            display_name = getattr(rec, "display_name", None) or getattr(rec, "name", None) or name
            input_schema = getattr(r, "input_schema", None) if r is not None else None
            output_schema = getattr(r, "output_schema", None) if r is not None else None
            version = getattr(r, "version", None) or getattr(rec, "version", None)
            enabled = bool(getattr(r, "enabled", False))
        except Exception as e:
            logger.warning("Failed to process plugin manifest entry %s: %s", name, e)
        out.append(
            PluginInfoResponse(
                name=name,
                version=version,
                enabled=enabled,
                display_name=display_name,
                input_schema=input_schema,
                output_schema=output_schema,
                capabilities=caps,
                required_identities=req_ids,
                op_auth=op_auth,
                default_feed_op=default_feed_op,
                allowed_feed_ops=allowed_feed_ops,
            )
        )

    return ShuResponse.success(sorted(out, key=lambda x: (not x.enabled, x.display_name.casefold())))


@router.get("/{name}", response_model=SuccessResponse[PluginInfoResponse])
async def get_plugin(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    row = res.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found")

    manifest = REGISTRY.get_manifest(refresh_if_empty=True)
    rec = manifest.get(row.name)
    caps = list(rec.capabilities or []) if rec and getattr(rec, "capabilities", None) is not None else None
    req_ids = (
        list(rec.required_identities or []) if rec and getattr(rec, "required_identities", None) is not None else None
    )
    op_auth = dict(rec.op_auth or {}) if rec and getattr(rec, "op_auth", None) is not None else None
    default_feed_op = rec.default_feed_op if rec and getattr(rec, "default_feed_op", None) is not None else None
    allowed_feed_ops = (
        list(rec.allowed_feed_ops or []) if rec and getattr(rec, "allowed_feed_ops", None) is not None else None
    )
    display_name = (
        (getattr(rec, "display_name", None) if rec else None) or (getattr(rec, "name", None) if rec else None) or name
    )

    return ShuResponse.success(
        PluginInfoResponse(
            name=row.name,
            version=getattr(row, "version", None),
            enabled=bool(row.enabled),
            display_name=display_name,
            input_schema=getattr(row, "input_schema", None),
            output_schema=getattr(row, "output_schema", None),
            capabilities=caps,
            required_identities=req_ids,
            op_auth=op_auth,
            default_feed_op=default_feed_op,
            allowed_feed_ops=allowed_feed_ops,
        )
    )


# TODO: Refactor this function. It's too complex (number of branches and statements).
@router.post("/{name}/execute")
async def execute_plugin(  # noqa: PLR0912, PLR0915
    name: str,
    body: PluginExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plugin = await REGISTRY.resolve(name, db)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found or disabled")

    # Preflight identity/scopes for clearer errors on direct execute
    try:
        from ..plugins.host.auth_capability import AuthCapability
        from ..services.plugin_identity import resolve_auth_requirements

        provider, mode_eff, _subject, scopes = resolve_auth_requirements(plugin, body.params or {})
        if provider and str(mode_eff or "").lower() == "user":
            # Execution-time subscription enforcement (TASK-163)
            try:
                from ..services.host_auth_service import HostAuthService

                subs = await HostAuthService.list_subscriptions(db, str(user.id), provider, None)
                if subs:
                    subscribed_names = {s.plugin_name for s in subs}
                    if str(name) not in subscribed_names:
                        try:
                            logger.warning(
                                "subscription.enforced | user=%s provider=%s plugin=%s path=execute",
                                str(user.id),
                                provider,
                                str(name),
                            )
                        except Exception:
                            pass
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": {
                                    "code": "subscription_required",
                                    "message": f"Plugin '{name}' is not subscribed for provider '{provider}'. Manage in Connected Accounts.",
                                    "provider": provider,
                                    "plugin": name,
                                }
                            },
                        )
            except HTTPException:
                raise
            except Exception:
                # If enforcement check fails unexpectedly, allow execution to proceed rather than blocking
                pass
            auth = AuthCapability(plugin_name=str(name), user_id=str(user.id))
            tok = await auth.provider_user_token(provider, required_scopes=scopes or None)
            if not tok:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": "insufficient_scopes",
                            "message": f"Provider account ({provider}) connected but missing required scopes for this operation. Reconnect via the plugin panel.",
                            "required_scopes": scopes or [],
                        }
                    },
                )
    except HTTPException:
        raise
    except Exception:
        pass

    # Preflight secrets for the op
    try:
        from ..services.plugin_identity import PluginIdentityError, ensure_secrets_for_plugin

        await ensure_secrets_for_plugin(plugin, name, str(user.id), body.params or {})
    except PluginIdentityError as pie:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": pie.code, "message": str(pie), "details": pie.details}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Secrets preflight check failed unexpectedly for plugin %s: %s", name, e)

    settings = get_settings_instance()
    enforce_input_limit(body.model_dump(), getattr(settings, "plugin_exec_input_max_bytes", 0))

    exec_rec = PluginExecution(
        plugin_name=name,
        user_id=str(user.id),
        agent_key=body.agent_key,
        params=body.params or {},
        status=PluginExecutionStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db.add(exec_rec)
    await db.commit()

    limits_row = await db.execute(select(PluginDefinition).where(PluginDefinition.name == name))
    limits_def = limits_row.scalars().first()
    per_plugin_limits = getattr(limits_def, "limits", None) or {}

    user_email_val = await resolve_user_email_for_execution(db, str(user.id), body.params, allow_impersonate=True)
    providers_map = await get_provider_identities_map(db, str(user.id))

    result = await EXECUTOR.execute(
        plugin=plugin,
        user_id=str(user.id),
        user_email=user_email_val,
        agent_key=body.agent_key,
        params=body.params or {},
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

    await enforce_output_limit(payload, getattr(settings, "plugin_exec_output_max_bytes", 0), exec_rec, db)

    exec_rec.result = payload
    exec_rec.completed_at = datetime.now(UTC)
    exec_rec.status = (
        PluginExecutionStatus.COMPLETED if payload.get("status") == "success" else PluginExecutionStatus.FAILED
    )
    _err_val = payload.get("error") if payload.get("status") != "success" else None
    if isinstance(_err_val, (dict, list)):
        exec_rec.error = json.dumps(_err_val, separators=(",", ":"), default=str)
    else:
        exec_rec.error = _err_val

    # Unified diagnostics logging (DRY)
    try:
        from ..plugins.utils import log_plugin_diagnostics as _log_diags

        _log_diags(payload, plugin_name=str(name), user_id=str(user.id))
    except Exception:
        pass

    await db.commit()

    return ShuResponse.success(payload)
