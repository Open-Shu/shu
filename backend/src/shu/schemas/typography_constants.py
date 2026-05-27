"""Shared typography constants for user preferences and branding schemas.

Single source of truth for the curated font family list and font size
scale tiers. Both `UserPreferencesUpdate` and `BrandingSettingsUpdate`
validate their font fields against these enums via `field_validator`.

Frontend mirrors these values in `frontend/src/utils/typography.js`; the
frontend test `typography.test.js` includes a parity check against this
list to catch drift.
"""

VALID_FONT_FAMILIES = [
    "system-ui",
    "inter",
    "roboto",
    "space-grotesk",
    "atkinson-hyperlegible",
    "lexend",
]

VALID_FONT_SIZE_SCALES = ["xs", "small", "default", "large", "xl"]

SHIPPED_DEFAULT_FONT = "inter"
