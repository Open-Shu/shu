"""Pydantic schemas for branding configuration endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrandingSettings(BaseModel):
    """Complete branding payload returned to clients."""

    logo_url: str = Field(default="/logo-wide.png")
    favicon_url: str = Field(default="/favicon.png")
    app_name: str | None = None
    light_theme_overrides: dict[str, Any] = Field(default_factory=dict)
    dark_theme_overrides: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None
    updated_by: str | None = None


class BrandingSettingsUpdate(BaseModel):
    """Partial update payload for branding settings."""

    model_config = ConfigDict(extra="allow")

    logo_url: str | None = None
    favicon_url: str | None = None
    app_name: str | None = None
    light_theme_overrides: dict[str, Any] | None = None
    dark_theme_overrides: dict[str, Any] | None = None
