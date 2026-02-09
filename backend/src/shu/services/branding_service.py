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

    async def update_branding(
        self,
        update: BrandingSettingsUpdate,
        *,
        user_id: str | None = None,
    ) -> BrandingSettings:
        stored = await self._system_settings.get_value(self.SETTINGS_KEY, {}) or {}
        update_data = update.model_dump(exclude_unset=True)

        for key, value in update_data.items():
            if value is None:
                stored.pop(key, None)
            else:
                stored[key] = value

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
        asset_type = asset_type.lower()
        if asset_type not in {"logo", "favicon"}:
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

        current = await self.get_branding()
        old_url = current.logo_url if asset_type == "logo" else current.favicon_url
        self._remove_local_asset(old_url, exclude_filename=asset_filename)

        field_name = "logo_url" if asset_type == "logo" else "favicon_url"
        update = BrandingSettingsUpdate(**{field_name: public_url})

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
        return {
            "logo_url": self.settings.branding_default_logo_url,
            "favicon_url": self.settings.branding_default_favicon_url,
            "app_name": self.settings.app_name,
            "light_theme_overrides": {},
            "dark_theme_overrides": {},
            "updated_at": None,
            "updated_by": None,
        }

    def _validate_asset(self, *, filename: str, file_bytes: bytes, asset_type: str) -> None:
        if not filename:
            raise ValueError("Filename is required")

        extension = Path(filename).suffix.lower().lstrip(".")
        allowed = (
            {ext.lower() for ext in self.settings.branding_allowed_logo_extensions}
            if asset_type == "logo"
            else {ext.lower() for ext in self.settings.branding_allowed_favicon_extensions}
        )

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
