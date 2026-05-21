"""Scaffolding for cross-tenant admin endpoints (SHU-761).

This module is intentionally empty of route handlers — the cross-tenant
admin surface is structural infrastructure landed alongside RLS, not a
launched feature. As real cross-tenant operations come up (debug a tenant
ticket, internal usage analytics, etc.) they get added here following the
two patterns sketched in the docstring below.

Routes added here MUST:
  * Take ``actor: User = Depends(require_internal_admin)`` as a dependency.
  * Take a ``reason: str`` body / query parameter — never default, never
    optional — that gets passed to the ``TenantAdminService`` call.
  * Delegate to ``TenantAdminService`` (no inline logic in the router).
  * Pick the right pattern:
      - "Operate as if I were tenant X" → ``impersonate_tenant``
      - "Aggregate across all tenants" → ``cross_tenant_query``

Example shapes (uncomment and adapt when you have a real endpoint):

    @router.post("/tenants/{tenant_id}/users")
    async def list_users_in_tenant(
        tenant_id: str,
        reason: str,
        actor: User = Depends(require_internal_admin),
        admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    ):
        async with admin_svc.impersonate_tenant(tenant_id, actor.id, reason) as session:
            return (await session.execute(select(User))).scalars().all()

    @router.get("/usage-summary")
    async def usage_summary(
        reason: str,
        actor: User = Depends(require_internal_admin),
        admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    ):
        async with admin_svc.cross_tenant_query(actor.id, reason) as session:
            return (await session.execute(
                select(LlmUsage.tenant_id, func.sum(LlmUsage.tokens))
                .group_by(LlmUsage.tenant_id)
            )).all()

The ``/admin`` prefix is applied here (not in ``main.py``) so the entire
surface is grep-able under a single namespace and no other router can
accidentally claim the path.
"""

from __future__ import annotations

from fastapi import APIRouter

from shu.core.database import get_admin_session_local, get_async_session_local
from shu.services.audit_logger import DefaultAuditLogger
from shu.services.tenant_admin_service import TenantAdminService

router = APIRouter(prefix="/admin", tags=["admin"])


def get_tenant_admin_service() -> TenantAdminService:
    """FastAPI dependency that wires the default ``TenantAdminService``.

    Kept module-local so callers ``Depends(get_tenant_admin_service)``
    rather than importing the class directly — the indirection is what
    lets tests override the dep with a stub via FastAPI's
    ``dependency_overrides``.
    """
    return TenantAdminService(
        app_session_local=get_async_session_local(),
        admin_session_local=get_admin_session_local(),
        audit_logger=DefaultAuditLogger(),
    )
