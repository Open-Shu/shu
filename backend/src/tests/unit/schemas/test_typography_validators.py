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

from shu.schemas.branding import BrandingSettingsUpdate
from shu.schemas.typography_constants import (
    SHIPPED_DEFAULT_FONT,
    VALID_FONT_FAMILIES,
    VALID_FONT_SIZE_SCALES,
)
from shu.schemas.user_preferences import UserPreferencesBase, UserPreferencesUpdate


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
