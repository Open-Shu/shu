"""BrandingService manages dynamic branding configuration and related assets."""

from __future__ import annotations

import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings, get_settings_instance
from ..schemas.branding import BrandingSettings, BrandingSettingsUpdate
from .system_settings_service import SystemSettingsService


def _build_default_payload(settings: Settings) -> dict[str, object]:
    """Return default branding payload, derived from the operator-configured env vars.

    Module-level so the instance method (``_default_payload``) and the
    classmethod (``defaults``) share one shape without touching ``self``.
    """
    return {
        "favicon_url": settings.branding_default_favicon_url,
        "app_name": settings.app_name,
        "light_theme_overrides": {},
        "dark_theme_overrides": {},
        "dark_favicon_url": settings.branding_default_dark_favicon_url,
        "light_topbar_text_color": None,
        "dark_topbar_text_color": None,
        "assistant_avatar_mode": "curated",
        "assistant_avatar_curated_id": "shu_feather",
        "assistant_avatar_asset_url": None,
        "brand_font_family": None,
        "brand_heading_font_family": None,
        "updated_at": None,
        "updated_by": None,
    }


class BrandingService:
    """Encapsulates branding configuration persistence and asset storage."""

    SETTINGS_KEY = "app.branding"

    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings_instance()
        self.assets_dir = Path(self.settings.branding_assets_dir).resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._system_settings = SystemSettingsService(db)

    async def get_branding(self) -> BrandingSettings:
        stored = await self._system_settings.get_value(self.SETTINGS_KEY, {}) or {}
        payload = self._default_payload()

        for key, value in stored.items():
            if value is None:
                continue
            payload[key] = value

        return BrandingSettings.model_validate(payload)

    @classmethod
    def defaults(cls, settings: Settings | None = None) -> BrandingSettings:
        """Build the default branding payload with no DB read.

        Used by the public ``GET /settings/branding`` route in multi-tenant
        mode, where the per-tenant ``system_settings`` row isn't readable
        without a tenant_context and the policy is "no per-tenant branding
        in MT" anyway. Skips the transaction we'd otherwise burn just to
        fall back to defaults.
        """
        settings = settings or get_settings_instance()
        return BrandingSettings.model_validate(_build_default_payload(settings))

    async def update_branding(
        self,
        update: BrandingSettingsUpdate,
        *,
        user_id: str | None = None,
    ) -> BrandingSettings:
        """Update branding configuration with validation.

        Args:
            update: Partial branding settings update
            user_id: Optional user ID for audit trail

        Returns:
            Updated branding configuration

        Raises:
            ValueError: If validation fails (e.g., invalid hex color format)

        """
        stored = await self._system_settings.get_value(self.SETTINGS_KEY, {}) or {}
        update_data = update.model_dump(exclude_unset=True)

        # Validate hex colors if provided
        for color_field in ["light_topbar_text_color", "dark_topbar_text_color"]:
            if (
                color_field in update_data
                and update_data[color_field] is not None
                and not self._is_valid_hex_color(update_data[color_field])
            ):
                raise ValueError(f"Invalid hex color format for {color_field}")

        # Snapshot the prior asset URL BEFORE the merge loop. The loop pops
        # the key when the update sets it to None, which would otherwise hide
        # the URL from the cleanup logic below and leave the file orphaned.
        prior_avatar_asset_url = stored.get("assistant_avatar_asset_url")

        for key, value in update_data.items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value

        # Avatar asset cleanup: the prior custom-mode upload is orphaned
        # unless the final state still references it. Covers three cases:
        #   - mode goes from "custom" to "curated"/"none"
        #   - assistant_avatar_asset_url is explicitly set to None while
        #     mode stays "custom"
        #   - assistant_avatar_asset_url is replaced with a different URL
        #     (the prior URL's file is no longer referenced)
        final_avatar_mode = stored.get("assistant_avatar_mode")
        final_avatar_url = stored.get("assistant_avatar_asset_url")
        prior_still_active = (
            prior_avatar_asset_url is not None
            and final_avatar_mode == "custom"
            and final_avatar_url == prior_avatar_asset_url
        )
        if prior_avatar_asset_url and not prior_still_active:
            self._remove_local_asset(prior_avatar_asset_url)
            # Keep stored state coherent: when mode is no longer "custom"
            # the URL field shouldn't carry a stale value either.
            if final_avatar_mode != "custom":
                stored.pop("assistant_avatar_asset_url", None)

        now = datetime.now(UTC).isoformat()
        stored["updated_at"] = now
        if user_id:
            stored["updated_by"] = user_id

        await self._system_settings.upsert(self.SETTINGS_KEY, stored)
        return await self.get_branding()

    async def save_asset(
        self,
        *,
        filename: str,
        file_bytes: bytes,
        asset_type: str,
        user_id: str | None = None,
    ) -> BrandingSettings:
        """Save a branding asset (favicon or dark mode favicon).

        Args:
            filename: Original filename of the asset
            file_bytes: Binary content of the asset file
            asset_type: Type of asset (favicon, dark_favicon)
            user_id: Optional user ID for audit trail

        Returns:
            Updated branding configuration

        Raises:
            ValueError: If asset type is unsupported or validation fails

        """
        asset_type = asset_type.lower()
        if asset_type not in {"favicon", "dark_favicon", "assistant_avatar"}:
            raise ValueError("Unsupported asset type")

        self._validate_asset(filename=filename, file_bytes=file_bytes, asset_type=asset_type)

        extension = Path(filename).suffix.lower()
        asset_filename = f"{asset_type}_{uuid.uuid4().hex}{extension}"
        asset_path = self.assets_dir / asset_filename

        try:
            asset_path.write_bytes(file_bytes)
        except Exception:
            if asset_path.exists():
                asset_path.unlink(missing_ok=True)
            raise

        public_url = f"{self.settings.api_v1_prefix}/settings/branding/assets/{asset_filename}"

        # Get current branding to find old asset
        current = await self.get_branding()
        old_url = self._get_old_asset_url(current, asset_type)
        self._remove_local_asset(old_url, exclude_filename=asset_filename)

        # Update configuration
        field_name = self._asset_type_to_field_name(asset_type)
        update_fields: dict[str, object] = {field_name: public_url}
        # An avatar upload implicitly switches mode to "custom" so the chat
        # renders the new image rather than the previously-selected curated
        # icon.
        if asset_type == "assistant_avatar":
            update_fields["assistant_avatar_mode"] = "custom"
        update = BrandingSettingsUpdate(**update_fields)

        try:
            return await self.update_branding(update, user_id=user_id)
        except Exception:
            # Roll back to prior state and delete uploaded asset if update fails.
            asset_path.unlink(missing_ok=True)
            raise

    def resolve_asset_path(self, filename: str) -> Path:
        safe_name = Path(filename).name
        if safe_name != filename:
            raise FileNotFoundError("Invalid asset name")

        path = (self.assets_dir / safe_name).resolve()
        if not path.exists():
            raise FileNotFoundError("Asset not found")

        # Ensure the resolved path is still inside the assets directory
        try:
            path.relative_to(self.assets_dir)
        except ValueError as exc:
            raise FileNotFoundError("Asset outside of allowed directory") from exc

        return path

    @staticmethod
    def guess_mime_type(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(str(path))
        return mime_type or "application/octet-stream"

    def _default_payload(self) -> dict[str, object]:
        return _build_default_payload(self.settings)

    def _validate_asset(self, *, filename: str, file_bytes: bytes, asset_type: str = "favicon") -> None:
        if not filename:
            raise ValueError("Filename is required")

        extension = Path(filename).suffix.lower().lstrip(".")
        if asset_type == "assistant_avatar":
            allowed = {ext.lower() for ext in self.settings.branding_allowed_avatar_extensions}
        else:
            allowed = {ext.lower() for ext in self.settings.branding_allowed_favicon_extensions}

        if extension not in allowed:
            raise ValueError(f"Invalid file type '.{extension}'. Allowed types: {', '.join(sorted(allowed))}")

        max_size = self.settings.branding_max_asset_size_bytes
        if len(file_bytes) > max_size:
            raise ValueError(f"File size {len(file_bytes)} exceeds limit of {max_size} bytes")

    def _remove_local_asset(self, url: str | None, *, exclude_filename: str | None = None) -> None:
        if not url:
            return

        prefix = f"{self.settings.api_v1_prefix}/settings/branding/assets/"
        if not url.startswith(prefix):
            return

        filename = url[len(prefix) :]
        if not filename:
            return

        safe_name = Path(filename).name
        if safe_name != filename:
            return

        if exclude_filename and safe_name == exclude_filename:
            return

        path = self.assets_dir / safe_name
        if path.exists():
            path.unlink(missing_ok=True)

    @staticmethod
    def _is_valid_hex_color(color: str) -> bool:
        """Validate hex color format (#RRGGBB or #RGB).

        Args:
            color: Color string to validate

        Returns:
            True if the color is a valid hex format, False otherwise

        """
        if not color.startswith("#"):
            return False
        hex_part = color[1:]
        if len(hex_part) not in {3, 6}:
            return False
        try:
            int(hex_part, 16)
            return True
        except ValueError:
            return False

    @staticmethod
    def _asset_type_to_field_name(asset_type: str) -> str:
        """Map asset type to configuration field name.

        Args:
            asset_type: Asset type (favicon, dark_favicon)

        Returns:
            Configuration field name for the asset type

        """
        mapping = {
            "favicon": "favicon_url",
            "dark_favicon": "dark_favicon_url",
            "assistant_avatar": "assistant_avatar_asset_url",
        }
        return mapping[asset_type]

    @staticmethod
    def _get_old_asset_url(branding: BrandingSettings, asset_type: str) -> str | None:
        """Get the current URL for an asset type.

        Args:
            branding: Current branding configuration
            asset_type: Asset type to retrieve URL for

        Returns:
            Current asset URL or None if not configured

        """
        field_name = BrandingService._asset_type_to_field_name(asset_type)
        return getattr(branding, field_name, None)
