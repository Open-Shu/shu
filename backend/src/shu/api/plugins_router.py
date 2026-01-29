"""Plugins API aggregator: includes public, admin, feeds, executions, and secrets sub-routers
All paths remain under /plugins
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.rbac import get_current_user
from ..schemas.envelope import SuccessResponse
from .dependencies import get_db
from .plugin_secrets import router as secrets_router
from .plugins_admin import router as admin_router
from .plugins_executions import router as exec_router
from .plugins_feeds import router as feeds_router
from .plugins_public import PluginInfoResponse
from .plugins_public import list_plugins as _list_plugins
from .plugins_public import router as public_router

router = APIRouter(prefix="/plugins", tags=["plugins"])  # final prefix namespaced by settings.api_v1_prefix

# Compose
router.include_router(public_router)
router.include_router(admin_router)
router.include_router(feeds_router)
router.include_router(exec_router)
router.include_router(secrets_router)


# Alias: allow GET /api/v1/plugins (no trailing slash) without 307 redirect
@router.get("", response_model=SuccessResponse[list[PluginInfoResponse]], include_in_schema=False)
async def list_plugins_no_slash(db=Depends(get_db), user=Depends(get_current_user)):
    return await _list_plugins(db, user)
