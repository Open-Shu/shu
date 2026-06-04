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

from shu.api.branding import (
    get_branding,
    patch_branding,
    upload_assistant_avatar,
    upload_favicon,
)
from shu.core.config import DeploymentMode
from shu.schemas.branding import BrandingSettingsUpdate
from shu.services.branding_service import BrandingService


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

    @pytest.mark.asyncio
    async def test_assistant_avatar_upload_returns_400_in_multi_tenant(self) -> None:
        db = AsyncMock()
        user = MagicMock(id="admin-1")
        upload_file = MagicMock()
        with patch(
            "shu.api.branding.get_settings_instance",
            return_value=_settings(DeploymentMode.MULTI_TENANT),
        ):
            with pytest.raises(Exception) as excinfo:
                await upload_assistant_avatar(
                    file=upload_file,
                    current_user=user,
                    db=db,
                )
        assert excinfo.value.status_code == 400
        assert "multi-tenant" in str(excinfo.value.detail).lower()


class TestAvatarValidationRejectsSVG:
    """SVG uploads must fail validation for ``assistant_avatar`` to keep the
    inline-SVG XSS vector closed if rendering ever shifts from ``<img>`` to
    inline ``<svg>`` (curated SVGs we control are still fine — those are
    bundled at build time, not uploaded by admins)."""

    def _service(self, tmp_path) -> BrandingService:
        db = AsyncMock()
        settings_stub = SimpleNamespace(
            branding_assets_dir=str(tmp_path),
            branding_default_favicon_url="/favicon.png",
            branding_default_dark_favicon_url="/favicon-dark.png",
            app_name="Shu",
            branding_allowed_favicon_extensions=["ico", "png", "svg", "webp"],
            branding_allowed_avatar_extensions=["png", "jpg", "jpeg", "webp"],
            branding_max_asset_size_bytes=2 * 1024 * 1024,
            api_v1_prefix="/api/v1",
        )
        return BrandingService(db, settings=settings_stub)

    def test_svg_rejected_for_avatar(self, tmp_path) -> None:
        service = self._service(tmp_path)
        with pytest.raises(ValueError, match="svg"):
            service._validate_asset(
                filename="oops.svg",
                file_bytes=b"<svg></svg>",
                asset_type="assistant_avatar",
            )

    def test_png_accepted_for_avatar(self, tmp_path) -> None:
        # No exception means accepted.
        self._service(tmp_path)._validate_asset(
            filename="ok.png",
            file_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            asset_type="assistant_avatar",
        )

    def test_svg_still_accepted_for_favicon(self, tmp_path) -> None:
        # The favicon path retains SVG support — only avatars exclude it.
        self._service(tmp_path)._validate_asset(
            filename="brand.svg",
            file_bytes=b"<svg></svg>",
            asset_type="favicon",
        )


class TestAvatarAssetOrphanCleanup:
    """update_branding must delete the prior custom-avatar file when the
    final state stops referencing it. Three triggers per ticket AC:
    mode change off "custom", explicit asset_url=None while mode stays
    "custom", and asset_url replacement. All three regressed at one
    point because the merge loop pops a key when the update value is
    None, hiding the URL from a naïve "if final_mode != custom" check.
    """

    def _service(self, tmp_path) -> BrandingService:
        db = AsyncMock()
        settings_stub = SimpleNamespace(
            branding_assets_dir=str(tmp_path),
            branding_default_favicon_url="/favicon.png",
            branding_default_dark_favicon_url="/favicon-dark.png",
            app_name="Shu",
            branding_allowed_favicon_extensions=["ico", "png", "svg", "webp"],
            branding_allowed_avatar_extensions=["png", "jpg", "jpeg", "webp"],
            branding_max_asset_size_bytes=2 * 1024 * 1024,
            api_v1_prefix="/api/v1",
        )
        return BrandingService(db, settings=settings_stub)

    def _seed_asset(self, service: BrandingService) -> tuple[str, "Path"]:  # type: ignore[name-defined]
        """Plant a fake uploaded avatar on disk and return (public_url, path)."""
        filename = "assistant_avatar_abc123.png"
        path = service.assets_dir / filename
        path.write_bytes(b"fake png bytes")
        public_url = f"{service.settings.api_v1_prefix}/settings/branding/assets/{filename}"
        return public_url, path

    async def _run_update(
        self,
        service: BrandingService,
        *,
        stored_before: dict,
        update: BrandingSettingsUpdate,
    ) -> dict:
        """Drive update_branding with a stubbed system_settings layer and
        return the dict that would have been upserted."""
        captured: dict = {}

        async def fake_get_value(_key: str, default):
            return dict(stored_before)

        async def fake_upsert(_key: str, value: dict):
            captured.update(value)

        service._system_settings.get_value = fake_get_value  # type: ignore[assignment]
        service._system_settings.upsert = fake_upsert  # type: ignore[assignment]
        # get_branding is called at the end for the return value; stub it
        # so it doesn't try to re-read system_settings.
        with patch.object(
            service, "get_branding", new=AsyncMock(return_value=MagicMock())
        ):
            await service.update_branding(update)
        return captured

    @pytest.mark.asyncio
    async def test_explicit_asset_url_null_in_custom_mode_deletes_file(self, tmp_path) -> None:
        # The case Codex flagged: admin PATCHes {asset_url: None} while
        # mode stays "custom". Pre-fix this left the file on disk because
        # the merge loop popped the URL before the cleanup block saw it.
        service = self._service(tmp_path)
        public_url, asset_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": public_url,
        }
        update = BrandingSettingsUpdate(assistant_avatar_asset_url=None)

        result = await self._run_update(service, stored_before=stored_before, update=update)

        assert not asset_path.exists(), "file on disk should have been deleted"
        assert "assistant_avatar_asset_url" not in result
        # Mode is preserved as 'custom' since the update didn't touch it,
        # even though the URL is now cleared. The frontend's resolveCuratedAvatar
        # fallback handles a custom mode with no asset (renders default).
        assert result.get("assistant_avatar_mode") == "custom"

    @pytest.mark.asyncio
    async def test_mode_switch_to_curated_deletes_file(self, tmp_path) -> None:
        # The originally-targeted case: mode flips off "custom" via PATCH
        # without an explicit asset_url update. The URL stays in the
        # merged dict so the cleanup block has access to it.
        service = self._service(tmp_path)
        public_url, asset_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": public_url,
        }
        update = BrandingSettingsUpdate(assistant_avatar_mode="curated", assistant_avatar_curated_id="shu_feather")

        result = await self._run_update(service, stored_before=stored_before, update=update)

        assert not asset_path.exists()
        assert "assistant_avatar_asset_url" not in result
        assert result.get("assistant_avatar_mode") == "curated"

    @pytest.mark.asyncio
    async def test_combined_mode_curated_and_explicit_url_null_deletes_file(self, tmp_path) -> None:
        # Combination case: PATCH sends BOTH mode='curated' and
        # asset_url=None in the same payload. Pre-fix the loop popped
        # the URL before cleanup could find it; the cleanup block then
        # saw final mode != 'custom' but no URL to delete.
        service = self._service(tmp_path)
        public_url, asset_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": public_url,
        }
        update = BrandingSettingsUpdate(
            assistant_avatar_mode="curated",
            assistant_avatar_asset_url=None,
        )

        result = await self._run_update(service, stored_before=stored_before, update=update)

        assert not asset_path.exists()
        assert "assistant_avatar_asset_url" not in result

    @pytest.mark.asyncio
    async def test_reset_to_defaults_payload_cleans_up_custom_avatar(self, tmp_path) -> None:
        # Simulates the exact payload the frontend's "Reset to Defaults"
        # button sends: every branding field nulled in a single PATCH,
        # including the three avatar fields. Verifies the orphan asset is
        # deleted and stored is fully cleared so the next read returns
        # defaults across the board.
        service = self._service(tmp_path)
        public_url, asset_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": public_url,
            "app_name": "Custom Org",
            "favicon_url": "/custom-favicon.png",
        }
        update = BrandingSettingsUpdate(
            app_name=None,
            favicon_url=None,
            dark_favicon_url=None,
            light_topbar_text_color=None,
            dark_topbar_text_color=None,
            light_theme_overrides=None,
            dark_theme_overrides=None,
            brand_font_family=None,
            brand_heading_font_family=None,
            assistant_avatar_mode=None,
            assistant_avatar_curated_id=None,
            assistant_avatar_asset_url=None,
        )

        result = await self._run_update(service, stored_before=stored_before, update=update)

        assert not asset_path.exists(), "reset must delete the orphaned custom avatar file"
        # All three avatar fields gone from stored — defaults take over on
        # the next read via BrandingService.defaults().
        assert "assistant_avatar_mode" not in result
        assert "assistant_avatar_curated_id" not in result
        assert "assistant_avatar_asset_url" not in result
        # Other branding fields also cleared to confirm the full reset.
        assert "app_name" not in result
        assert "favicon_url" not in result

    @pytest.mark.asyncio
    async def test_save_asset_writes_file_and_flips_mode_to_custom(self, tmp_path) -> None:
        # save_asset is the upload path used by POST /settings/branding/assistant-avatar.
        # The contract it must satisfy:
        #   - file bytes land on disk under branding_assets_dir
        #   - filename is UUID-prefixed (cache-busting on the client)
        #   - assistant_avatar_asset_url ends up in stored, pointing at the
        #     served route
        #   - assistant_avatar_mode flips to 'custom' atomically so the chat
        #     renders the new image instead of the previously-selected
        #     curated icon
        service = self._service(tmp_path)
        captured: dict = {}

        async def fake_get_value(_key: str, default):
            # Simulate a tenant currently in curated mode (shu_feather default).
            return {
                "assistant_avatar_mode": "curated",
                "assistant_avatar_curated_id": "shu_feather",
            }

        async def fake_upsert(_key: str, value: dict):
            captured.update(value)

        service._system_settings.get_value = fake_get_value  # type: ignore[assignment]
        service._system_settings.upsert = fake_upsert  # type: ignore[assignment]
        with patch.object(service, "get_branding", new=AsyncMock(return_value=MagicMock())):
            await service.save_asset(
                filename="logo.png",
                file_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
                asset_type="assistant_avatar",
                user_id="admin-1",
            )

        # File on disk: exactly one PNG written under assets_dir, UUID-named.
        written = list(service.assets_dir.glob("assistant_avatar_*.png"))
        assert len(written) == 1, f"expected 1 file, found {written}"

        # Mode flipped to 'custom'; URL points at the just-written file.
        assert captured.get("assistant_avatar_mode") == "custom"
        assert captured.get("assistant_avatar_asset_url", "").startswith(
            "/api/v1/settings/branding/assets/assistant_avatar_"
        )
        assert captured.get("assistant_avatar_asset_url", "").endswith(".png")
        # Audit trail propagated.
        assert captured.get("updated_by") == "admin-1"

    @pytest.mark.asyncio
    async def test_save_asset_replacement_deletes_prior_file(self, tmp_path) -> None:
        # Uploading a second avatar must remove the first file from disk.
        # The existing _remove_local_asset path on save_asset handles this
        # (separately from the update_branding orphan cleanup tested above).
        service = self._service(tmp_path)
        stored: dict = {}

        async def fake_get_value(_key: str, default):
            return dict(stored)

        async def fake_upsert(_key: str, value: dict):
            stored.clear()
            stored.update(value)

        service._system_settings.get_value = fake_get_value  # type: ignore[assignment]
        service._system_settings.upsert = fake_upsert  # type: ignore[assignment]

        # Pre-seed an "existing" custom upload: file on disk + DB pointing at it.
        seed_url, seed_path = self._seed_asset(service)
        stored["assistant_avatar_mode"] = "custom"
        stored["assistant_avatar_asset_url"] = seed_url
        assert seed_path.exists()

        with patch.object(service, "get_branding", new=AsyncMock(return_value=MagicMock())):
            await service.save_asset(
                filename="logo2.png",
                file_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
                asset_type="assistant_avatar",
                user_id="admin-1",
            )

        # Prior file gone, exactly one new file remains.
        assert not seed_path.exists(), "prior upload should have been deleted"
        remaining = list(service.assets_dir.glob("assistant_avatar_*.png"))
        assert len(remaining) == 1
        assert remaining[0] != seed_path
        # Stored URL points at the new file.
        assert stored.get("assistant_avatar_asset_url", "").endswith(remaining[0].name)
        assert stored.get("assistant_avatar_mode") == "custom"

    @pytest.mark.asyncio
    async def test_db_failure_during_replacement_preserves_prior_file(self, tmp_path) -> None:
        # CodeRabbit review case: if the DB upsert fails while replacing an
        # avatar, the prior file must NOT be deleted — otherwise the DB
        # still points to a now-missing URL (broken image in chat). Drives
        # save_asset against an upsert that raises and asserts the prior
        # file survives.
        service = self._service(tmp_path)
        seed_url, seed_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": seed_url,
        }

        async def fake_get_value(_key: str, default):
            return dict(stored_before)

        async def fake_upsert(_key: str, _value: dict):
            raise RuntimeError("simulated DB failure during upsert")

        service._system_settings.get_value = fake_get_value  # type: ignore[assignment]
        service._system_settings.upsert = fake_upsert  # type: ignore[assignment]

        with patch.object(service, "get_branding", new=AsyncMock(return_value=MagicMock())):
            with pytest.raises(RuntimeError, match="simulated DB failure"):
                await service.save_asset(
                    filename="replacement.png",
                    file_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
                    asset_type="assistant_avatar",
                    user_id="admin-1",
                )

        # Prior file must still exist — DB write failed, so we must not
        # have deleted it. The newly-uploaded file is rolled back by
        # save_asset's existing except handler.
        assert seed_path.exists(), "prior file must survive a failed upsert"
        new_uploads = [p for p in service.assets_dir.glob("assistant_avatar_*.png") if p != seed_path]
        assert new_uploads == [], "new upload must be rolled back on DB failure"

    @pytest.mark.asyncio
    async def test_unchanged_custom_url_is_preserved(self, tmp_path) -> None:
        # Regression guard: a no-op PATCH (or one that touches unrelated
        # fields) must NOT delete the in-use avatar file. Without the
        # `prior_still_active` short-circuit, our cleanup might delete
        # the live asset on every save.
        service = self._service(tmp_path)
        public_url, asset_path = self._seed_asset(service)
        stored_before = {
            "assistant_avatar_mode": "custom",
            "assistant_avatar_asset_url": public_url,
        }
        update = BrandingSettingsUpdate(app_name="My App")

        result = await self._run_update(service, stored_before=stored_before, update=update)

        assert asset_path.exists(), "in-use avatar must not be deleted"
        assert result.get("assistant_avatar_asset_url") == public_url
        assert result.get("assistant_avatar_mode") == "custom"
