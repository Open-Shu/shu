"""Shared API-test fixtures.

Routes that exercise framework-level behavior (Depends() resolution, exception
handler wiring, response Content-Type) need a real FastAPI app to observe the
behavior under test. Spinning up the production app via main.py drags in DB
init and settings validation; this helper keeps the surface minimal.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from shu.api.dependencies import get_db
from shu.auth.rbac import (
    get_current_user,
    require_admin,
    require_power_user,
    require_regular_user,
)
from shu.billing.cp_client import HEALTHY_DEFAULT
from shu.billing.entitlements import EntitlementSet
from shu.core.exceptions import ShuException


def make_app_with_router(router: APIRouter, *, prefix: str = "/api/v1") -> FastAPI:
    """Build a minimal FastAPI app mounting `router` and the production ShuException handler.

    The handler shape mirrors main.py's setup_exception_handlers — replicated
    here rather than imported because the production wiring pulls in
    get_settings_instance and the full app config.
    """
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    @app.exception_handler(ShuException)
    async def _shu_exception_handler(request, exc: ShuException):  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    return app


# ---------------------------------------------------------------------------
# SHU-773 entitlement-gating helpers
#
# These verify the `require_entitlement` dependency is *wired* onto a router —
# which only fires through FastAPI's Depends() resolution, so they need a real
# app + TestClient (same justification as the SHU-703 gate test in
# test_knowledge_bases.py). Shared here so each router's test file can pin its
# own gate without duplicating the scaffolding.
# ---------------------------------------------------------------------------


def entitlement_state(**entitlements):
    """HEALTHY_DEFAULT with the entitlement set overridden, for install_stub_cache."""
    return dataclasses.replace(HEALTHY_DEFAULT, entitlements=EntitlementSet(**entitlements))


def gated_app(router: APIRouter) -> FastAPI:
    """Mount `router` with auth deps overridden so only an entitlement gate can 403."""
    app = make_app_with_router(router)
    fake_user = MagicMock()
    fake_user.id = "u1"
    fake_user.can_manage_users.return_value = True
    fake_user.has_role.return_value = True
    for dep in (get_current_user, require_admin, require_power_user, require_regular_user):
        app.dependency_overrides[dep] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    return app


def assert_entitlement_denied(response, key: str) -> bool:
    """True when `response` is the 403 entitlement_denied envelope for `key`."""
    if response.status_code != 403:
        return False
    err = response.json().get("error", {})
    return err.get("code") == "entitlement_denied" and err.get("details", {}).get("entitlement") == key
