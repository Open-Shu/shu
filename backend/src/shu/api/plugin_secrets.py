"""Plugin secrets API endpoints.

Admin endpoints (``/plugins/admin/{name}/secrets*``) manage secrets with
configurable scope (user or system). User endpoints (``/plugins/self/{name}/secrets*``)
manage the current user's secrets only.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel

from ..auth.rbac import require_power_user, get_current_user
from ..auth.models import User
from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..plugins.host._storage_ops import normalize_scope
from ..services.plugin_secrets import (
    list_secret_keys,
    list_secrets_meta,
    set_secret,
    delete_secret,
    purge_old_secrets,
)

router = APIRouter()


def _validate_scope_and_user(scope: Optional[str], user_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """Normalize scope and validate user_id requirement.

    Returns:
        Tuple of (normalized_scope, user_id)

    Raises:
        HTTPException: If scope='user' but user_id is not provided.
    """
    normalized_scope = normalize_scope(scope)
    if normalized_scope == "user" and not user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user_id is required when scope='user'",
        )
    return normalized_scope, user_id


class PluginSecretSetRequest(BaseModel):
    user_id: Optional[str] = None
    value: str
    scope: Optional[str] = "user"  # "user" or "system"


class PluginSelfSecretSetRequest(BaseModel):
    value: str


class PluginSecretMeta(BaseModel):
    key: str
    updated_at: datetime


class PluginSecretsListResponse(BaseModel):
    name: str
    user_id: Optional[str] = None
    scope: str = "user"
    keys: List[str] = []
    items: List[PluginSecretMeta] = []


@router.get("/admin/{name}/secrets", response_model=SuccessResponse[PluginSecretsListResponse])
async def admin_list_plugin_secrets(
    name: str,
    user_id: Optional[str] = Query(None, description="User id (required for scope='user')"),
    scope: str = Query("user", description="Secret scope: 'user' or 'system'"),
    include_meta: bool = Query(False),
    _admin: User = Depends(require_power_user),  # Auth side-effect only
):
    normalized_scope, _ = _validate_scope_and_user(scope, user_id)

    if include_meta:
        rows = await list_secrets_meta(name, user_id=user_id, scope=normalized_scope)
        keys = [k for k, _ in rows]
        items = [PluginSecretMeta(key=k, updated_at=ts) for k, ts in rows]
        return ShuResponse.success(
            PluginSecretsListResponse(name=name, user_id=user_id, scope=normalized_scope, keys=keys, items=items)
        )
    else:
        keys = await list_secret_keys(name, user_id=user_id, scope=normalized_scope)
        return ShuResponse.success(PluginSecretsListResponse(name=name, user_id=user_id, scope=normalized_scope, keys=keys))


@router.put("/admin/{name}/secrets/{key}")
async def admin_set_plugin_secret(
    name: str,
    key: str,
    body: PluginSecretSetRequest,
    admin: User = Depends(require_power_user),
):
    scope_norm, _ = _validate_scope_and_user(body.scope, body.user_id)
    target_user_id: Optional[str] = body.user_id
    if scope_norm == "system" and not target_user_id:
        # For system scope, user_id is set to the admin's user ID to serve as an audit trail
        # of who created or updated the secret.
        target_user_id = str(admin.id)

    await set_secret(name, key, user_id=str(target_user_id), value=body.value, scope=scope_norm)
    return ShuResponse.success({"status": "ok", "scope": scope_norm})


@router.delete("/admin/{name}/secrets/{key}")
async def admin_delete_plugin_secret(
    name: str,
    key: str,
    user_id: Optional[str] = Query(None, description="User id (required for scope='user')"),
    scope: str = Query("user", description="Secret scope: 'user' or 'system'"),
    _admin: User = Depends(require_power_user),  # Auth side-effect only
):
    normalized_scope, _ = _validate_scope_and_user(scope, user_id)
    await delete_secret(name, key, user_id=user_id, scope=normalized_scope)
    return ShuResponse.success({"status": "deleted", "scope": normalized_scope})


@router.delete("/admin/{name}/secrets/_purge")
async def admin_purge_plugin_secrets(
    name: str,
    user_id: Optional[str] = Query(None, description="User id (required for scope='user')"),
    older_than_days: int = 90,
    scope: str = Query("user", description="Secret scope: 'user' or 'system'"),
    _admin: User = Depends(require_power_user),  # Auth side-effect only
):
    normalized_scope, _ = _validate_scope_and_user(scope, user_id)
    deleted = await purge_old_secrets(name, user_id=user_id, older_than_days=older_than_days, scope=normalized_scope)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(older_than_days)))
    return ShuResponse.success({
        "status": "purged",
        "deleted": int(deleted),
        "cutoff": cutoff.isoformat(),
        "scope": normalized_scope,
    })


@router.get("/self/{name}/secrets", response_model=SuccessResponse[PluginSecretsListResponse])
async def self_list_plugin_secrets(
    name: str,
    include_meta: bool = Query(False),
    user: User = Depends(get_current_user),
):
    """List the current user's secrets for a plugin."""
    uid = str(user.id)
    if include_meta:
        rows = await list_secrets_meta(name, user_id=uid, scope="user")
        keys = [k for k, _ in rows]
        items = [PluginSecretMeta(key=k, updated_at=ts) for k, ts in rows]
        return ShuResponse.success(
            PluginSecretsListResponse(name=name, user_id=uid, scope="user", keys=keys, items=items)
        )
    else:
        keys = await list_secret_keys(name, user_id=uid, scope="user")
        return ShuResponse.success(PluginSecretsListResponse(name=name, user_id=uid, scope="user", keys=keys))


@router.put("/self/{name}/secrets/{key}")
async def self_set_plugin_secret(
    name: str,
    key: str,
    body: PluginSelfSecretSetRequest,
    user: User = Depends(get_current_user),
):
    await set_secret(name, key, user_id=str(user.id), value=body.value, scope="user")
    return ShuResponse.success({"status": "ok", "scope": "user"})


@router.delete("/self/{name}/secrets/{key}")
async def self_delete_plugin_secret(
    name: str,
    key: str,
    user: User = Depends(get_current_user),
):
    await delete_secret(name, key, user_id=str(user.id), scope="user")
    return ShuResponse.success({"status": "deleted", "scope": "user"})
