"""
Pydantic schemas for branding configuration endpoints.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class BrandingSettings(BaseModel):
    """Complete branding payload returned to clients."""

    logo_url: str = Field(default="/logo-wide.png")
    favicon_url: str = Field(default="/favicon.png")
    app_name: Optional[str] = None
    light_theme_overrides: Dict[str, Any] = Field(default_factory=dict)
    dark_theme_overrides: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class BrandingSettingsUpdate(BaseModel):
    """Partial update payload for branding settings."""

    model_config = ConfigDict(extra="allow")

    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    app_name: Optional[str] = None
    light_theme_overrides: Optional[Dict[str, Any]] = None
    dark_theme_overrides: Optional[Dict[str, Any]] = None
