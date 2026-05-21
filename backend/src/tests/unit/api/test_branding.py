"""Unit tests for the branding route.

The route is on a public path (auth middleware skips), so there's no
ambient tenant_context. After H17 the underlying ``system_settings``
table is RLS-scoped — reading it without a context default-denies. Two
shapes the route must enforce:

* Multi-tenant: short-circuit to operator-configured defaults without
  touching the DB. PATCH / favicon-upload are rejected so admins don't
  see a silent "Branding saved" / "still default" contradiction.
* Self-hosted / silo: wrap the read in ``tenant_context_for_tenant_id``
  so the deployment's saved overrides are visible under RLS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.branding import get_branding, patch_branding, upload_favicon
from shu.core.config import DeploymentMode
from shu.schemas.branding import BrandingSettingsUpdate


def _settings(mode: DeploymentMode, *, tenant_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        deployment_mode=mode,
        tenant_id=tenant_id or "deployment-tenant-uuid",
        redis_namespace=None,
        branding_default_favicon_url="/static/favicon.png",
        branding_default_dark_favicon_url="/static/favicon-dark.png",
        app_name="Shu",
    )


class TestGetBrandingShortCircuitsInMultiTenant:
    @pytest.mark.asyncio
    async def test_multi_tenant_returns_defaults_without_touching_db(self) -> None:
        db = AsyncMock()
        settings_stub = _settings(DeploymentMode.MULTI_TENANT)
        with patch(
            "shu.api.branding.get_settings_instance",
            return_value=settings_stub,
        ), patch(
            # BrandingService.defaults uses get_settings_instance from
            # the service module to look up branding env values.
            "shu.services.branding_service.get_settings_instance",
            return_value=settings_stub,
        ):
            response = await get_branding(db=db)

        assert response.data.app_name == "Shu"
        assert response.data.favicon_url == "/static/favicon.png"
        # No DB session ops — short-circuit dispatches before any read.
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_hosted_wraps_read_in_deployment_tenant_context(self) -> None:
        """The route must set tenant_context before the service reads
        ``system_settings`` — without it RLS default-denies and the saved
        overrides come back empty (silently falling back to defaults even
        when the operator has configured branding)."""
        from shu.core.config import SELF_HOSTED_TENANT_UUID
        from shu.core.tenant import tenant_context

        observed_tid: list[str | None] = []

        async def fake_get_branding(self_svc):
            observed_tid.append(tenant_context.get(None))
            return MagicMock(model_dump=MagicMock(return_value={}))

        db = AsyncMock()
        with patch(
            "shu.api.branding.get_settings_instance",
            return_value=_settings(DeploymentMode.SELF_HOSTED),
        ), patch(
            "shu.core.tenant.get_settings_instance",
            return_value=_settings(DeploymentMode.SELF_HOSTED),
        ), patch.object(
            __import__("shu.services.branding_service", fromlist=["BrandingService"]).BrandingService,
            "get_branding",
            fake_get_branding,
            create=False,
        ):
            await get_branding(db=db)

        assert observed_tid == [SELF_HOSTED_TENANT_UUID]


class TestPatchBrandingRejectsInMultiTenant:
    @pytest.mark.asyncio
    async def test_patch_returns_400_in_multi_tenant(self) -> None:
        db = AsyncMock()
        user = MagicMock(id="admin-1")
        with patch(
            "shu.api.branding.get_settings_instance",
            return_value=_settings(DeploymentMode.MULTI_TENANT),
        ):
            with pytest.raises(Exception) as excinfo:
                await patch_branding(
                    payload=BrandingSettingsUpdate(),
                    current_user=user,
                    db=db,
                )
        assert excinfo.value.status_code == 400
        assert "multi-tenant" in str(excinfo.value.detail).lower()

    @pytest.mark.asyncio
    async def test_favicon_upload_returns_400_in_multi_tenant(self) -> None:
        db = AsyncMock()
        user = MagicMock(id="admin-1")
        upload_file = MagicMock()
        with patch(
            "shu.api.branding.get_settings_instance",
            return_value=_settings(DeploymentMode.MULTI_TENANT),
        ):
            with pytest.raises(Exception) as excinfo:
                await upload_favicon(
                    file=upload_file,
                    theme="light",
                    current_user=user,
                    db=db,
                )
        assert excinfo.value.status_code == 400
