from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...core.logging import get_logger
from ...core.ocr_modes import OcrMode, coerce_ocr_mode
from ...core.ocr_service import extract_text_with_ocr_fallback
from .base import ImmutableCapabilityMixin

if TYPE_CHECKING:
    from ...core.config import ConfigurationManager

logger = get_logger(__name__)


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
    _ocr_mode: OcrMode | None

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        config_manager: ConfigurationManager,
        ocr_mode: str | OcrMode | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_config_manager", config_manager)
        # Distinguish "no mode supplied" (None) from "invalid string" (also None
        # for backward-compatible silent ignore). `extract_text` defaults to AUTO
        # when neither the constructor nor the call-site passes a valid value.
        if ocr_mode is None:
            resolved: OcrMode | None = None
        elif isinstance(ocr_mode, OcrMode):
            resolved = ocr_mode
        else:
            s = ocr_mode.strip().lower()
            try:
                resolved = OcrMode(s)
            except ValueError:
                resolved = None
        object.__setattr__(self, "_ocr_mode", resolved)

    async def extract_text(self, *, file_bytes: bytes, mime_type: str, mode: str | None = None) -> dict[str, Any]:
        mm = coerce_ocr_mode(mode if mode is not None else self._ocr_mode, default=OcrMode.AUTO)

        # Forward user_id so the resulting llm_usage row attributes to the
        # plugin's acting user, matching the ingestion-worker OCR path.
        # Dropping it here would leave every plugin-initiated OCR row with
        # NULL user_id — same class of bug as the extract_text_with_ocr_fallback
        # auto/fallback drop caught earlier in SHU-700.
        res = await extract_text_with_ocr_fallback(
            file_bytes=file_bytes,
            mime_type=mime_type,
            config_manager=self._config_manager,
            ocr_mode=mm,
            user_id=self._user_id,
        )

        logger.info(
            "host.ocr.extract_text",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "mime_type": mime_type,
                "mode": mm.value,
            },
        )
        return res
