from __future__ import annotations

import logging

from ...processors.text_extractor import TextExtractor
from .base import ImmutableCapabilityMixin

logger = logging.getLogger(__name__)


class OcrCapability(ImmutableCapabilityMixin):
    """OCR/Text extraction utility for plugins.

    Note: Treated as a utility without implicit host policy. Per-feed OCR policy is
    enforced by host.kb ingestion. Callers may pass mode explicitly if needed.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to bypass audit logging.
    """

    __slots__ = ("_ocr_mode", "_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str
    _ocr_mode: str | None

    def __init__(self, *, plugin_name: str, user_id: str, ocr_mode: str | None = None) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        mode = (ocr_mode or "").strip().lower()
        object.__setattr__(self, "_ocr_mode", mode if mode in {"auto", "always", "never", "fallback"} else None)

    async def extract_text(self, *, file_bytes: bytes, mime_type: str, mode: str | None = None) -> dict[str, Any]:
        mm = (mode or self._ocr_mode or "auto").strip().lower()
        extractor = TextExtractor()
        res = await extractor.extract_text(file_bytes=file_bytes, mime_type=mime_type, mode=mm)
        # Best-effort log with minimal PII
        try:
            logger.info(
                "host.ocr.extract_text",
                extra={
                    "plugin": self._plugin_name,
                    "user_id": self._user_id,
                    "mime_type": mime_type,
                    "mode": mm,
                },
            )
        except Exception:
            pass
        return res
