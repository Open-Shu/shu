from __future__ import annotations

import logging
import mimetypes
from typing import TYPE_CHECKING, Any

from ...processors.text_extractor import TextExtractor
from .base import ImmutableCapabilityMixin

if TYPE_CHECKING:
    from ...core.config import ConfigurationManager

logger = logging.getLogger(__name__)

# Valid OCR mode values accepted by the ingestion pipeline.
_VALID_OCR_MODES = {"auto", "always", "never", "fallback", "text_only"}


class OcrCapability(ImmutableCapabilityMixin):
    """OCR/Text extraction utility for plugins.

    Note: Treated as a utility without implicit host policy. Per-feed OCR policy is
    enforced by host.kb ingestion. Callers may pass mode explicitly if needed.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to bypass audit logging.
    """

    __slots__ = ("_config_manager", "_ocr_mode", "_plugin_name", "_user_id")

    _config_manager: ConfigurationManager
    _plugin_name: str
    _user_id: str
    _ocr_mode: str | None

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        config_manager: ConfigurationManager,
        ocr_mode: str | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_config_manager", config_manager)
        mode = (ocr_mode or "").strip().lower()
        object.__setattr__(self, "_ocr_mode", mode if mode in _VALID_OCR_MODES else None)

    async def extract_text(self, *, file_bytes: bytes, mime_type: str, mode: str | None = None) -> dict[str, Any]:
        mm = (mode or self._ocr_mode or "auto").strip().lower()

        # Derive a synthetic filename so TextExtractor can detect the file type
        # from the extension.  mimetypes.guess_extension returns e.g. ".pdf".
        ext = mimetypes.guess_extension(mime_type) or ".bin"
        synthetic_path = f"plugin_upload{ext}"

        # Translate OCR mode string → use_ocr bool + progress_context.
        # "text_only" / "never" disable OCR; everything else enables it and
        # lets the extractor decide based on content.
        use_ocr = mm not in {"text_only", "never"}

        extractor = TextExtractor(config_manager=self._config_manager)
        res = await extractor.extract_text(
            file_path=synthetic_path,
            file_content=file_bytes,
            use_ocr=use_ocr,
            progress_context={"ocr_mode": mm} if mm != "auto" else None,
        )

        logger.info(
            "host.ocr.extract_text",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "mime_type": mime_type,
                "mode": mm,
            },
        )
        return res
