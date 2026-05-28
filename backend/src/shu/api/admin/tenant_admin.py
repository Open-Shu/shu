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

from fastapi import APIRouter, Depends

from shu.auth.password_auth import PasswordAuthService
from shu.billing.router_envelope import verify_router_envelope_dep
from shu.core.database import get_admin_session_local, get_async_session_local
from shu.schemas.cp_provisioning import (
    CreateTenantRequest,
    CreateTenantResponse,
    SetModelConfigsRequest,
    SetModelConfigsResponse,
    SetPoliciesRequest,
    SetPoliciesResponse,
    SetPromptRequest,
    SetPromptResponse,
    SetUserActiveRequest,
    SetUserActiveResponse,
)
from shu.services.audit_logger import AuditLogger, DefaultAuditLogger
from shu.services.model_configuration_service import ModelConfigurationService
from shu.services.password_reset_service import get_password_reset_service_dependency
from shu.services.policy_service import PolicyService
from shu.services.prompt_service import PromptService
from shu.services.tenant_admin_service import TenantAdminService
from shu.services.user_service import UserService

router = APIRouter(prefix="/admin", tags=["admin"])

# `cp_router` carries the CP-driven provisioning surface (SHU-785). It lives
# alongside `router` in this module so the entire `/admin/*` namespace stays
# grep-able in one place, but uses a different auth dependency — HMAC
# envelope verification — because the caller is the Control Plane process,
# not a human admin. The `verify_router_envelope_dep` declared at the router
# level inherits onto every handler attached to `cp_router`, so individual
# handlers do not need to repeat it.
cp_router = APIRouter(
    prefix="/admin/cp",
    tags=["admin", "cp"],
    dependencies=[Depends(verify_router_envelope_dep)],
)


def get_tenant_admin_service() -> TenantAdminService:
    """FastAPI dependency that wires the default ``TenantAdminService``.

    Kept module-local so callers ``Depends(get_tenant_admin_service)``
    rather than importing the class directly — the indirection is what
    lets tests override the dep with a stub via FastAPI's
    ``dependency_overrides``.

    The CP-only collaborators (``PasswordAuthService``,
    ``PasswordResetService``) are wired here too so the same
    ``TenantAdminService`` instance can serve both human-admin endpoints
    and CP provisioning endpoints — there's no per-call switch over the
    operating mode, just whether the new method gets called.
    """
    return TenantAdminService(
        app_session_local=get_async_session_local(),
        admin_session_local=get_admin_session_local(),
        audit_logger=DefaultAuditLogger(),
        password_auth=PasswordAuthService(),
        password_reset=get_password_reset_service_dependency(),
    )


# Each CP service dep gets its own factory rather than inlining the
# construction at every handler so route handlers stay one line of
# `Depends(...)` per collaborator. The `db=None` placeholder is acceptable
# because cp_* methods open their own session via the injected
# `tenant_admin_svc` and never touch `self.db`.


def get_cp_audit_logger() -> AuditLogger:
    return DefaultAuditLogger()


def get_cp_model_configuration_service(
    admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    audit: AuditLogger = Depends(get_cp_audit_logger),
) -> ModelConfigurationService:
    return ModelConfigurationService(
        db=None,
        tenant_admin_svc=admin_svc,
        audit_logger=audit,
    )


def get_cp_policy_service(
    admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    audit: AuditLogger = Depends(get_cp_audit_logger),
) -> PolicyService:
    return PolicyService(
        db=None,
        tenant_admin_svc=admin_svc,
        audit_logger=audit,
    )


def get_cp_prompt_service(
    admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    audit: AuditLogger = Depends(get_cp_audit_logger),
) -> PromptService:
    return PromptService(
        db=None,
        tenant_admin_svc=admin_svc,
        audit_logger=audit,
    )


def get_cp_user_service() -> UserService:
    return UserService()


# ---------------------------------------------------------------------------
# CP-driven tenant provisioning endpoints (SHU-785)
# ---------------------------------------------------------------------------


@cp_router.post("/tenants", response_model=CreateTenantResponse)
async def cp_create_tenant(
    payload: CreateTenantRequest,
    admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
) -> CreateTenantResponse:
    return await admin_svc.create_tenant(payload, payload.reason)


@cp_router.put(
    "/tenants/{tenant_id}/model-configs",
    response_model=SetModelConfigsResponse,
)
async def cp_set_model_configs(
    tenant_id: str,
    payload: SetModelConfigsRequest,
    mc_svc: ModelConfigurationService = Depends(get_cp_model_configuration_service),
) -> SetModelConfigsResponse:
    return await mc_svc.cp_upsert_by_name(tenant_id, payload, payload.reason)


@cp_router.put(
    "/tenants/{tenant_id}/policies",
    response_model=SetPoliciesResponse,
)
async def cp_set_policies(
    tenant_id: str,
    payload: SetPoliciesRequest,
    policy_svc: PolicyService = Depends(get_cp_policy_service),
) -> SetPoliciesResponse:
    return await policy_svc.cp_replace_and_bind(tenant_id, payload, payload.reason)


@cp_router.put(
    "/tenants/{tenant_id}/prompt",
    response_model=SetPromptResponse,
)
async def cp_set_prompt(
    tenant_id: str,
    payload: SetPromptRequest,
    prompt_svc: PromptService = Depends(get_cp_prompt_service),
) -> SetPromptResponse:
    return await prompt_svc.cp_upsert_by_name(tenant_id, payload, payload.reason)


@cp_router.patch(
    "/tenants/{tenant_id}/user/active",
    response_model=SetUserActiveResponse,
)
async def cp_set_user_active(
    tenant_id: str,
    payload: SetUserActiveRequest,
    user_svc: UserService = Depends(get_cp_user_service),
    admin_svc: TenantAdminService = Depends(get_tenant_admin_service),
    audit: AuditLogger = Depends(get_cp_audit_logger),
) -> SetUserActiveResponse:
    return await user_svc.cp_set_user_active(
        tenant_id,
        payload.is_active,
        payload.reason,
        tenant_admin_svc=admin_svc,
        audit_logger=audit,
    )
