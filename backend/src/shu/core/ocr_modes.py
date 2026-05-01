"""Single source of truth for the OCR mode enum.

Lives in its own module to avoid the import cycle that would arise if it
were defined in `ocr_service.py` (which imports `TextExtractor`, which would
otherwise need to import the mode type back).
"""

from __future__ import annotations

from enum import StrEnum


class OcrMode(StrEnum):
    """Public OCR routing mode for the ingestion pipeline.

    - ``AUTO``: classify the PDF and route to OCR or text extraction based on
      the per-page real-text classifier. The default for ingestion.
    - ``ALWAYS``: skip the classifier and always run OCR (falls back to text
      extraction for non-OCR-eligible types).
    - ``NEVER``: text extraction only, no OCR under any circumstances.

    The legacy values ``"fallback"`` (was an alias for ``AUTO``) and
    ``"text_only"`` (was an alias for ``NEVER``) were removed in SHU-728.
    """

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


def coerce_ocr_mode(value: str | OcrMode | None, default: OcrMode = OcrMode.AUTO) -> OcrMode:
    """Coerce an untrusted external value to an `OcrMode`.

    Used at all entry points where the mode arrives as a string from a worker
    job payload, plugin host context, or user-supplied API parameter.
    Unknown values fall back to ``default`` rather than raising — preserves
    the prior leniency at plugin/worker boundaries.
    """
    if isinstance(value, OcrMode):
        return value
    if not value:
        return default
    if not isinstance(value, str):
        # Type hint says str | OcrMode | None; an int/list/dict here means a
        # bad caller. Match the existing leniency contract and return default
        # rather than AttributeError on .strip().
        return default
    s = value.strip().lower()
    try:
        return OcrMode(s)
    except ValueError:
        return default
