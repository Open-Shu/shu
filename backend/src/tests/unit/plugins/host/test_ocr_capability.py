"""Unit tests for OcrCapability (host.ocr plugin capability).

Uses the same direct-module-load pattern as test_kb_capability.py to avoid
triggering the full shu.plugins.host package __init__, which pulls in heavy
capability dependencies that aren't needed for isolated capability tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"


def _load_module(module_name: str, file_path: Path):
    """Load a module directly from its file path, registering it in sys.modules."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module '{module_name}' from '{file_path}'")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load base (needed by ocr_capability)
_load_module("shu.plugins.host.base", _HOST_DIR / "base.py")

# Stub out shu.core.ocr_service so ocr_capability's `from ...core.ocr_service
# import extract_text_with_ocr_fallback` resolves cleanly without loading the
# real module (which pulls in heavy dependencies via the TextExtractor chain).
#
# Scope: the stub only needs to be in sys.modules long enough for _load_module
# below to import ocr_capability — at which point _ocr_cap_mod has already
# captured the stub's `extract_text_with_ocr_fallback` attribute into its own
# namespace. After that, sys.modules["shu.core.ocr_service"] is restored so
# other test modules that import the real module see it, and our tests use
# patch.object(_ocr_cap_mod, ...) against the already-captured reference.
# A module-teardown fixture is NOT sufficient here because pytest imports all
# test modules during collection before running any tests — a late teardown
# would leak the stub into every test module collected afterwards.
_previous_ocr_service = sys.modules.get("shu.core.ocr_service")
if _previous_ocr_service is None:
    _ocr_service_stub = MagicMock()
    _ocr_service_stub.extract_text_with_ocr_fallback = AsyncMock()
    sys.modules["shu.core.ocr_service"] = _ocr_service_stub

_ocr_cap_mod = _load_module("shu.plugins.host.ocr_capability", _HOST_DIR / "ocr_capability.py")
OcrCapability = _ocr_cap_mod.OcrCapability

# Restore sys.modules immediately. _ocr_cap_mod has already captured the stub
# via its `from ... import extract_text_with_ocr_fallback`, so the in-namespace
# reference survives even after the stub is popped from sys.modules.
if _previous_ocr_service is None:
    del sys.modules["shu.core.ocr_service"]


def _make_capability(user_id: str = "user-1", plugin_name: str = "test_plugin") -> "OcrCapability":
    return OcrCapability(
        plugin_name=plugin_name,
        user_id=user_id,
        config_manager=MagicMock(),
        ocr_mode=None,
    )


class TestUserIdThreading:
    """SHU-700 regression guard: host.ocr.extract_text must forward the acting
    user's ID into the OCR pipeline. Dropping it at this boundary would leave
    every plugin-initiated OCR row in llm_usage with NULL user_id, breaking
    per-user billing attribution for plugin-driven OCR workloads.
    """

    @pytest.mark.asyncio
    async def test_user_id_is_forwarded_to_ocr_fallback(self):
        cap = _make_capability(user_id="user-abc")

        with patch.object(
            _ocr_cap_mod,
            "extract_text_with_ocr_fallback",
            new_callable=AsyncMock,
        ) as mock_fallback:
            mock_fallback.return_value = {"text": "ok", "metadata": {}}
            await cap.extract_text(file_bytes=b"%PDF-fake", mime_type="application/pdf")

        mock_fallback.assert_awaited_once()
        assert mock_fallback.call_args.kwargs.get("user_id") == "user-abc", (
            "host.ocr.extract_text must forward user_id to extract_text_with_ocr_fallback"
        )

    @pytest.mark.asyncio
    async def test_mode_override_still_forwards_user_id(self):
        """Caller-supplied mode override must not disturb user_id forwarding."""
        cap = _make_capability(user_id="user-xyz")

        with patch.object(
            _ocr_cap_mod,
            "extract_text_with_ocr_fallback",
            new_callable=AsyncMock,
        ) as mock_fallback:
            mock_fallback.return_value = {"text": "ok", "metadata": {}}
            await cap.extract_text(file_bytes=b"data", mime_type="image/png", mode="always")

        kwargs = mock_fallback.call_args.kwargs
        assert kwargs.get("user_id") == "user-xyz"
        assert kwargs.get("ocr_mode") == "always"
