"""Pydantic schemas for branding configuration endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .typography_constants import VALID_FONT_FAMILIES


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

    # Typography branding fields (null = use shipped default)
    brand_font_family: str | None = None
    brand_heading_font_family: str | None = None


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

    # Typography branding fields
    brand_font_family: str | None = None
    brand_heading_font_family: str | None = None

    @field_validator("brand_font_family", "brand_heading_font_family")
    @classmethod
    def validate_brand_font(cls, v: str | None) -> str | None:
        """Validate brand font family is in the curated list."""
        if v is not None and v not in VALID_FONT_FAMILIES:
            raise ValueError(f"font must be one of: {VALID_FONT_FAMILIES}")
        return v
