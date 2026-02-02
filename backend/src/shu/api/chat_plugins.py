"""Chat Plugins API router: per-op descriptors for LLM plugin-calling and minimal execution facade.
M1 scope: expose read-only ops only (as declared by plugin manifest chat_callable_ops).
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.response import ShuResponse
from ..models.plugin_registry import PluginDefinition
from ..plugins.base import Plugin
from ..plugins.executor import EXECUTOR
from ..plugins.loader import PluginRecord
from ..plugins.registry import REGISTRY
from ..schemas.envelope import SuccessResponse
from ..services.plugin_identity import (
    PluginIdentityError,
    ensure_secrets_for_plugin,
    ensure_user_identity_for_plugin,
    get_provider_identities_map,
    resolve_user_email_for_execution,
)

router = APIRouter(prefix="/chat/plugins", tags=["chat-plugins"])  # prefixed by settings.api_v1_prefix in main


class ChatPluginOpDescriptor(BaseModel):
    name: str
    op: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    required_identities: list[dict[str, Any]] | None = None
    chat_callable: bool = True


class ChatPluginListResponse(BaseModel):
    plugins: list[ChatPluginOpDescriptor] = Field(default_factory=list)


class ChatPluginExecuteRequest(BaseModel):
    name: str
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    agent_key: str | None = None


def get_chat_ops(rec: PluginRecord) -> list:
    try:
        return list(getattr(rec, "chat_callable_ops", []) or [])
    except Exception:
        return []


async def get_plugin_and_schema(name: str, db: AsyncSession) -> tuple[Plugin | None, dict[str, Any] | None]:
    plugin = None
    try:
        plugin = await REGISTRY.resolve(name, db)
    except Exception:
        pass

    schema = None
    try:
        schema = plugin.get_schema()
    except Exception:
        pass

    return plugin, schema


def get_enum_labels(schema: dict[str, Any] | None) -> dict[str, str]:
    try:
        return ((((schema or {}).get("properties") or {}).get("op") or {}).get("x-ui", {})).get("enum_labels")
    except Exception:
        return None


@router.get("", response_model=SuccessResponse[ChatPluginListResponse])
async def list_chat_plugins(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Ensure manifest loaded
    try:
        manifest = getattr(REGISTRY, "_manifest", {}) or {}
        if not manifest:
            REGISTRY.refresh()
            manifest = getattr(REGISTRY, "_manifest", {}) or {}
    except Exception:
        manifest = {}

    # Get enabled plugins from DB
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.enabled == True))  # noqa: E712
    rows = res.scalars().all()
    enabled = {r.name for r in rows}

    out: list[ChatPluginOpDescriptor] = []
    for name, rec in (manifest or {}).items():
        if name not in enabled:
            continue

        chat_ops = get_chat_ops(rec)
        if not chat_ops:
            continue

        # Load plugin to get schema and optional labels/help
        plugin, schema = await get_plugin_and_schema(name, db)
        if not plugin:
            continue

        # Derive enum labels/help for title/description when available
        enum_labels = get_enum_labels(schema)

        for op in chat_ops:
            title = None
            description = None
            if isinstance(enum_labels, dict):
                label = enum_labels.get(str(op))
                if label:
                    title = label
            # Fallback titles
            if not title:
                title = f"{name}:{op}"
            base_schema = schema or {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
            # Deep-copy the base schema so we can pin the `op` field per chat-callable operation
            # without mutating the plugin's original schema (shared across ops/endpoints). This limits
            # the available operations to the chat-callable subset declared in the manifest, which prevents
            # op injection attacks or LLM accidental execution of non-chat-callable ops.
            op_schema = copy.deepcopy(base_schema)
            props = op_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [op],
                "const": op,
                "default": op,
            }
            if isinstance(op_schema.get("required"), list):
                if "op" not in op_schema["required"]:
                    op_schema["required"].append("op")
            else:
                op_schema["required"] = ["op"]
            out.append(
                ChatPluginOpDescriptor(
                    name=name,
                    op=op,
                    title=title,
                    description=description,
                    input_schema=op_schema,
                    required_identities=list(getattr(rec, "required_identities", []) or []),
                    chat_callable=True,
                )
            )

    return ShuResponse.success(ChatPluginListResponse(plugins=out))


class ChatPluginExecuteResponse(BaseModel):
    status: str
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@router.post("/execute", response_model=SuccessResponse[ChatPluginExecuteResponse])
async def execute_chat_plugin(
    body: ChatPluginExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Validate plugin is enabled and op is declared chat-callable
    try:
        manifest = getattr(REGISTRY, "_manifest", {}) or {}
        if not manifest:
            REGISTRY.refresh()
            manifest = getattr(REGISTRY, "_manifest", {}) or {}
    except Exception:
        manifest = {}
    rec = manifest.get(body.name)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Plugin '{body.name}' not found")
    res = await db.execute(select(PluginDefinition).where(PluginDefinition.name == body.name))
    row = res.scalars().first()
    if not row or not row.enabled:
        raise HTTPException(status_code=404, detail=f"Plugin '{body.name}' not found or disabled")
    chat_ops = list(getattr(rec, "chat_callable_ops", []) or [])
    if body.op not in chat_ops:
        raise HTTPException(status_code=400, detail=f"Op '{body.op}' is not chat-callable for plugin '{body.name}'")

    plugin = await REGISTRY.resolve(body.name, db)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{body.name}' not found or disabled")

    # Gather per-plugin limits (optional) from PluginDefinition.limits JSON
    limits = getattr(row, "limits", {}) or {}

    # Build provider identities map and resolve user email via shared helpers
    providers_map = await get_provider_identities_map(db, str(user.id))

    # Ensure op is set in params for executor
    params = dict(body.params or {})
    params["op"] = body.op

    try:
        await ensure_user_identity_for_plugin(db, plugin, body.name, str(user.id), params)
        await ensure_secrets_for_plugin(plugin, body.name, str(user.id), params)
    except PluginIdentityError as pie:
        detail = {"error": {"code": pie.code, "message": str(pie)}}
        if pie.details:
            detail["error"]["details"] = pie.details
        raise HTTPException(status_code=403, detail=detail)

    user_email_val = await resolve_user_email_for_execution(db, str(user.id), params, allow_impersonate=True)

    result = await EXECUTOR.execute(
        plugin=plugin,
        user_id=str(user.id),
        user_email=user_email_val,
        agent_key=body.agent_key,
        params=params,
        limits=limits,
        provider_identities=providers_map,
    )

    try:
        payload = result.model_dump()
    except Exception:
        payload = {
            "status": getattr(result, "status", None),
            "data": getattr(result, "data", None),
            "error": getattr(result, "error", None),
        }

    # Unified diagnostics logging (DRY)
    try:
        from ..plugins.utils import log_plugin_diagnostics as _log_diags

        _log_diags(payload, plugin_name=str(body.name))
    except Exception:
        pass
    return ShuResponse.success(ChatPluginExecuteResponse(**payload))
