"""Shared typography constants and validator types for user preferences and branding schemas.

Single source of truth for the curated font family list and font size
scale tiers. The `FontFamilyKey` and `FontSizeScaleKey` `Annotated`
aliases below carry validation into the type itself, so any schema
that declares a field with one of these types automatically rejects
out-of-list values on both write and read paths — no `field_validator`
boilerplate needed at each call site.

Frontend mirrors these values in `frontend/src/utils/typography.js`; the
frontend test `typography.test.js` includes a parity check against this
list to catch drift.
"""

from typing import Annotated

from pydantic import AfterValidator

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


def _validate_font_family(v: str | None) -> str | None:
    """Reject font family keys outside the curated list. None passes through."""
    if v is not None and v not in VALID_FONT_FAMILIES:
        raise ValueError(f"font_family must be one of: {VALID_FONT_FAMILIES}")
    return v


def _validate_font_size_scale(v: str | None) -> str | None:
    """Reject scale tier keys outside the curated list. None passes through."""
    if v is not None and v not in VALID_FONT_SIZE_SCALES:
        raise ValueError(f"font_size_scale must be one of: {VALID_FONT_SIZE_SCALES}")
    return v


# Type aliases — use these instead of `str | None` on any schema field
# that should be validated against the curated typography enums.
FontFamilyKey = Annotated[str | None, AfterValidator(_validate_font_family)]
FontSizeScaleKey = Annotated[str | None, AfterValidator(_validate_font_size_scale)]
