"""Pydantic schemas for User Preferences API.

Defines request/response models for user preferences including:
- Memory settings validation
- Search & RAG settings validation
- Chat behavior settings validation
- UI/UX preferences validation
- Advanced settings (JSON) validation
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .typography_constants import VALID_FONT_FAMILIES, VALID_FONT_SIZE_SCALES

# Valid theme options - centralized to avoid divergence
VALID_THEMES = ["light", "dark", "auto"]


class UserPreferencesBase(BaseModel):
    """Base schema for user preferences with validation."""

    # Memory Settings
    memory_depth: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of previous conversations to consider for memory",
    )
    memory_similarity_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, description="Similarity threshold for memory retrieval"
    )

    # NOTE: RAG and LLM settings removed - these should be admin-only configuration
    # Users should not be able to override KB or model configuration settings
    # Removed: default_search_threshold, default_max_results, default_context_format,
    # default_reference_format, default_temperature, default_max_tokens

    # UI/UX Preferences
    theme: str = Field(default="light", description="UI theme preference")
    language: str = Field(default="en", min_length=2, max_length=10, description="Language preference (ISO code)")
    timezone: str = Field(default="UTC", description="User timezone")

    # Typography preferences (null = inherit from branding / shipped default)
    font_family: str | None = Field(default=None, description="Body font family")
    font_size_scale: str | None = Field(default=None, description="Font size scale tier")

    # Advanced Settings
    advanced_settings: dict[str, Any] | None = Field(
        default_factory=dict, description="Additional custom settings as JSON"
    )

    # NOTE: Validators for removed RAG/LLM settings deleted

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str) -> str:
        """Validate theme options."""
        if v not in VALID_THEMES:
            raise ValueError(f"Theme must be one of: {VALID_THEMES}")
        return v

    @field_validator("font_family")
    @classmethod
    def validate_font_family(cls, v: str | None) -> str | None:
        """Validate font family is in the curated list."""
        if v is not None and v not in VALID_FONT_FAMILIES:
            raise ValueError(f"font_family must be one of: {VALID_FONT_FAMILIES}")
        return v

    @field_validator("font_size_scale")
    @classmethod
    def validate_font_size_scale(cls, v: str | None) -> str | None:
        """Validate font size scale tier."""
        if v is not None and v not in VALID_FONT_SIZE_SCALES:
            raise ValueError(f"font_size_scale must be one of: {VALID_FONT_SIZE_SCALES}")
        return v


class UserPreferencesCreate(UserPreferencesBase):
    """Schema for creating user preferences."""

    pass


class UserPreferencesUpdate(BaseModel):
    """Schema for partially updating user preferences."""

    # Memory Settings
    memory_depth: int | None = Field(None, ge=1, le=20)
    memory_similarity_threshold: float | None = Field(None, ge=0.0, le=1.0)

    # NOTE: RAG and LLM settings removed - these should be admin-only configuration

    # UI/UX Preferences
    theme: str | None = None
    language: str | None = Field(None, min_length=2, max_length=10)
    timezone: str | None = None

    # Typography preferences
    font_family: str | None = None
    font_size_scale: str | None = None

    # Advanced Settings
    advanced_settings: dict[str, Any] | None = None

    # NOTE: Validators for removed RAG/LLM settings deleted

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str | None) -> str | None:
        """Validate theme options."""
        if v is not None and v not in VALID_THEMES:
            raise ValueError(f"Theme must be one of: {VALID_THEMES}")
        return v

    @field_validator("font_family")
    @classmethod
    def validate_font_family(cls, v: str | None) -> str | None:
        """Validate font family is in the curated list."""
        if v is not None and v not in VALID_FONT_FAMILIES:
            raise ValueError(f"font_family must be one of: {VALID_FONT_FAMILIES}")
        return v

    @field_validator("font_size_scale")
    @classmethod
    def validate_font_size_scale(cls, v: str | None) -> str | None:
        """Validate font size scale tier."""
        if v is not None and v not in VALID_FONT_SIZE_SCALES:
            raise ValueError(f"font_size_scale must be one of: {VALID_FONT_SIZE_SCALES}")
        return v


class UserPreferencesResponse(BaseModel):
    """Schema for user preferences API responses."""

    # Memory Settings
    memory_depth: int
    memory_similarity_threshold: float

    # NOTE: RAG and LLM settings removed - these should be admin-only configuration

    # UI/UX Preferences
    theme: str
    language: str
    timezone: str

    # Typography preferences (nullable — null = inherit from branding)
    font_family: str | None
    font_size_scale: str | None

    # Advanced Settings
    advanced_settings: dict[str, Any]

    @field_validator("font_family")
    @classmethod
    def validate_response_font_family(cls, v: str | None) -> str | None:
        """Reject legacy/direct-DB values outside the curated list so the
        frontend Select doesn't render an unknown option silently.
        """
        if v is not None and v not in VALID_FONT_FAMILIES:
            raise ValueError(f"font_family must be one of: {VALID_FONT_FAMILIES}")
        return v

    @field_validator("font_size_scale")
    @classmethod
    def validate_response_font_size_scale(cls, v: str | None) -> str | None:
        """Reject legacy/direct-DB values outside the curated list."""
        if v is not None and v not in VALID_FONT_SIZE_SCALES:
            raise ValueError(f"font_size_scale must be one of: {VALID_FONT_SIZE_SCALES}")
        return v

    # System-provided read-only configuration
    summary_search_min_token_length: int
    summary_search_max_tokens: int

    model_config = ConfigDict(from_attributes=True)
