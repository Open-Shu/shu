"""Pydantic schemas for branding configuration endpoints."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AssistantAvatarMode = Literal["curated", "custom", "none"]


class BrandingSettings(BaseModel):
    """Complete branding payload returned to clients."""

    favicon_url: str = Field(default="/favicon-dark.png")
    app_name: str | None = None
    light_theme_overrides: dict[str, Any] = Field(default_factory=dict)
    dark_theme_overrides: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None
    updated_by: str | None = None

    # Theme-aware branding fields
    dark_favicon_url: str | None = None
    light_topbar_text_color: str | None = None
    dark_topbar_text_color: str | None = None

    # Assistant avatar (SHU-815). curated_id resolves to a frontend-bundled
    # component; asset_url is populated only when mode == "custom".
    assistant_avatar_mode: AssistantAvatarMode = "curated"
    assistant_avatar_curated_id: str | None = "shu_feather"
    assistant_avatar_asset_url: str | None = None


class BrandingSettingsUpdate(BaseModel):
    """Partial update payload for branding settings."""

    model_config = ConfigDict(extra="allow")

    favicon_url: str | None = None
    app_name: str | None = None
    light_theme_overrides: dict[str, Any] | None = None
    dark_theme_overrides: dict[str, Any] | None = None

    # Theme-aware branding fields
    dark_favicon_url: str | None = None
    light_topbar_text_color: str | None = None
    dark_topbar_text_color: str | None = None

    # Assistant avatar (SHU-815)
    assistant_avatar_mode: AssistantAvatarMode | None = None
    assistant_avatar_curated_id: str | None = None
    assistant_avatar_asset_url: str | None = None
