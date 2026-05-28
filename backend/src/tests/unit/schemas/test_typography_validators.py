"""Validator coverage for SHU-811 typography fields.

The picker UI only sends curated values, but the API is callable
directly (curl, devtools, scripts). Without server-side enforcement
a bad value would persist silently and break the cascade for that
user. These tests mirror the existing `theme` validator pattern at
`schemas/user_preferences.py:validate_theme`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shu.schemas.branding import BrandingSettings, BrandingSettingsUpdate
from shu.schemas.typography_constants import (
    SHIPPED_DEFAULT_FONT,
    VALID_FONT_FAMILIES,
    VALID_FONT_SIZE_SCALES,
)
from shu.schemas.user_preferences import UserPreferencesBase, UserPreferencesResponse, UserPreferencesUpdate


class TestUserPreferencesFontFamilyValidator:
    def test_accepts_each_curated_value(self) -> None:
        for value in VALID_FONT_FAMILIES:
            prefs = UserPreferencesUpdate(font_family=value)
            assert prefs.font_family == value

    def test_accepts_none(self) -> None:
        """null = "inherit from branding"; must remain a legal value."""
        prefs = UserPreferencesUpdate(font_family=None)
        assert prefs.font_family is None

    @pytest.mark.parametrize("bad_value", ["Comic Sans MS", "Wingdings", "", "INTER", "arial"])
    def test_rejects_uncurated_values(self, bad_value: str) -> None:
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(font_family=bad_value)

    def test_validator_also_applies_to_create_payload(self) -> None:
        """`UserPreferencesBase` is the parent of `UserPreferencesCreate`; the
        validator must also reject bad data on the PUT path."""
        with pytest.raises(ValidationError):
            UserPreferencesBase(font_family="Comic Sans MS")


class TestUserPreferencesFontSizeScaleValidator:
    def test_accepts_each_curated_tier(self) -> None:
        for value in VALID_FONT_SIZE_SCALES:
            prefs = UserPreferencesUpdate(font_size_scale=value)
            assert prefs.font_size_scale == value

    def test_accepts_none(self) -> None:
        prefs = UserPreferencesUpdate(font_size_scale=None)
        assert prefs.font_size_scale is None

    @pytest.mark.parametrize("bad_value", ["medium", "XL", "huge", "1.2", ""])
    def test_rejects_uncurated_tiers(self, bad_value: str) -> None:
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(font_size_scale=bad_value)


class TestBrandingFontValidators:
    def test_brand_font_family_accepts_each_curated_value(self) -> None:
        for value in VALID_FONT_FAMILIES:
            payload = BrandingSettingsUpdate(brand_font_family=value)
            assert payload.brand_font_family == value

    def test_brand_heading_font_family_accepts_each_curated_value(self) -> None:
        for value in VALID_FONT_FAMILIES:
            payload = BrandingSettingsUpdate(brand_heading_font_family=value)
            assert payload.brand_heading_font_family == value

    def test_both_accept_none(self) -> None:
        payload = BrandingSettingsUpdate(brand_font_family=None, brand_heading_font_family=None)
        assert payload.brand_font_family is None
        assert payload.brand_heading_font_family is None

    @pytest.mark.parametrize("bad_value", ["Comic Sans MS", "Wingdings", "garbage"])
    def test_rejects_uncurated_brand_font(self, bad_value: str) -> None:
        with pytest.raises(ValidationError):
            BrandingSettingsUpdate(brand_font_family=bad_value)

    @pytest.mark.parametrize("bad_value", ["Comic Sans MS", "Wingdings", "garbage"])
    def test_rejects_uncurated_brand_heading_font(self, bad_value: str) -> None:
        with pytest.raises(ValidationError):
            BrandingSettingsUpdate(brand_heading_font_family=bad_value)


class TestResponseSchemaValidators:
    """Read-schema validators guard against legacy / direct-DB bad data leaking
    through GET responses to the frontend. Writes are already protected by the
    Update schemas; this is the defensive read-side layer."""

    def _response_kwargs(self, **overrides: object) -> dict[str, object]:
        return {
            "memory_depth": 5,
            "memory_similarity_threshold": 0.6,
            "theme": "light",
            "language": "en",
            "timezone": "UTC",
            "font_family": None,
            "font_size_scale": None,
            "advanced_settings": {},
            # System-provided read-only fields surfaced in the response envelope.
            "summary_search_min_token_length": 4,
            "summary_search_max_tokens": 8000,
            **overrides,
        }

    def test_user_response_accepts_curated_font_family(self) -> None:
        response = UserPreferencesResponse.model_validate(self._response_kwargs(font_family="space-grotesk"))
        assert response.font_family == "space-grotesk"

    def test_user_response_accepts_null_font_family(self) -> None:
        response = UserPreferencesResponse.model_validate(self._response_kwargs(font_family=None))
        assert response.font_family is None

    def test_user_response_rejects_invalid_font_family(self) -> None:
        with pytest.raises(ValidationError):
            UserPreferencesResponse.model_validate(self._response_kwargs(font_family="Comic Sans MS"))

    def test_user_response_rejects_invalid_font_size_scale(self) -> None:
        with pytest.raises(ValidationError):
            UserPreferencesResponse.model_validate(self._response_kwargs(font_size_scale="huge"))

    def test_branding_response_accepts_curated_brand_font(self) -> None:
        branding = BrandingSettings.model_validate({"brand_font_family": "atkinson-hyperlegible"})
        assert branding.brand_font_family == "atkinson-hyperlegible"

    def test_branding_response_rejects_invalid_brand_font(self) -> None:
        with pytest.raises(ValidationError):
            BrandingSettings.model_validate({"brand_font_family": "Wingdings"})

    def test_branding_response_rejects_invalid_brand_heading_font(self) -> None:
        with pytest.raises(ValidationError):
            BrandingSettings.model_validate({"brand_heading_font_family": "Comic Sans MS"})


class TestSharedConstants:
    def test_shipped_default_is_in_curated_list(self) -> None:
        """The cascade resolves to SHIPPED_DEFAULT_FONT; if that key
        weren't in the curated list, FONT_FAMILIES[SHIPPED_DEFAULT_FONT]
        would be undefined on the frontend and the cascade would crash."""
        assert SHIPPED_DEFAULT_FONT in VALID_FONT_FAMILIES

    def test_default_scale_tier_present(self) -> None:
        """The frontend defaults to "default" tier when font_size_scale
        is null; ensure that tier exists in the enum."""
        assert "default" in VALID_FONT_SIZE_SCALES
