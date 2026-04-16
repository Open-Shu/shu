"""Unit tests for IngestCapability and make_host ingest wiring.

Covers:
- from_http happy path: fetch + ingest delegation
- HttpRequestFailed propagation (ingest not called)
- Ingest-phase exception propagation (fetch still called)
- make_host ValueError when ingest requested without http+kb
- make_host wiring: ingest slot populated when http+kb+ingest declared

Import strategy: modules are loaded directly from file paths to avoid
triggering the full shu.plugins.host package __init__, which would pull
in heavy service dependencies. This mirrors test_kb_capability.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from _module_loader import load_module as _load_module

_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"


_base_mod = _load_module("shu.plugins.host.base", _HOST_DIR / "base.py")
_exceptions_mod = _load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")
HttpRequestFailed = _exceptions_mod.HttpRequestFailed

# Stub heavy service deps so ingest_capability and kb_capability can load
for _stub_name in (
    "shu.services.ingestion_service",
    "shu.services.knowledge_object_service",
    "shu.knowledge",
    "shu.knowledge.ko",
):
    if _stub_name not in sys.modules:
        sys.modules[_stub_name] = MagicMock()

if "shu.core" not in sys.modules:
    sys.modules["shu.core"] = MagicMock()
if "shu.core.database" not in sys.modules:
    _db_stub = MagicMock()
    _db_stub.get_db_session = AsyncMock()
    sys.modules["shu.core.database"] = _db_stub
if "shu.core.config" not in sys.modules:
    _config_stub = MagicMock()
    _config_stub.get_settings_instance = MagicMock(return_value=MagicMock())
    sys.modules["shu.core.config"] = _config_stub
if "shu.core.http_client" not in sys.modules:
    sys.modules["shu.core.http_client"] = MagicMock()

# Load the real capability modules we need
_kb_cap_mod = _load_module("shu.plugins.host.kb_capability", _HOST_DIR / "kb_capability.py")
_http_cap_mod = _load_module("shu.plugins.host.http_capability", _HOST_DIR / "http_capability.py")
_ingest_cap_mod = _load_module("shu.plugins.host.ingest_capability", _HOST_DIR / "ingest_capability.py")
IngestCapability = _ingest_cap_mod.IngestCapability

# Stub remaining capability modules used by host_builder
for _cap_stub in (
    "shu.plugins.host.auth_capability",
    "shu.plugins.host.cache_capability",
    "shu.plugins.host.cursor_capability",
    "shu.plugins.host.identity_capability",
    "shu.plugins.host.log_capability",
    "shu.plugins.host.ocr_capability",
    "shu.plugins.host.secrets_capability",
    "shu.plugins.host.storage_capability",
    "shu.plugins.host.utils_capability",
):
    if _cap_stub not in sys.modules:
        sys.modules[_cap_stub] = MagicMock()

_host_builder_mod = _load_module("shu.plugins.host.host_builder", _HOST_DIR / "host_builder.py")
make_host = _host_builder_mod.make_host


class TestFromHttp:
    """Tests for IngestCapability.from_http delegation logic."""

    @pytest.mark.asyncio
    async def test_happy_path_delegates_fetch_then_ingest(self):
        """from_http calls http.fetch_bytes then kb.ingest_document and returns the ingest result."""
        http = MagicMock()
        http.fetch_bytes = AsyncMock(
            return_value={"content": b"pdf bytes", "status_code": 200, "headers": {}}
        )
        kb = MagicMock()
        kb.ingest_document = AsyncMock(return_value={"doc_id": "123"})

        cap = IngestCapability(plugin_name="test", user_id="u1", http=http, kb=kb)
        result = await cap.from_http(
            "kb-1",
            method="GET",
            url="https://example.com/file.pdf",
            filename="file.pdf",
            mime_type="application/pdf",
            source_id="ext-1",
        )

        assert result == {"doc_id": "123"}

        http.fetch_bytes.assert_awaited_once_with(
            "GET", "https://example.com/file.pdf", headers={}, params={},
        )
        kb.ingest_document.assert_awaited_once_with(
            "kb-1",
            file_bytes=b"pdf bytes",
            filename="file.pdf",
            mime_type="application/pdf",
            source_id="ext-1",
            source_url=None,
            attributes=None,
        )

    @pytest.mark.asyncio
    async def test_http_request_failed_propagates_unchanged(self):
        """HttpRequestFailed from fetch_bytes propagates without calling ingest."""
        http = MagicMock()
        http.fetch_bytes = AsyncMock(
            side_effect=HttpRequestFailed(
                status_code=403, url="https://x", body=None, headers={},
            )
        )
        kb = MagicMock()
        kb.ingest_document = AsyncMock()

        cap = IngestCapability(plugin_name="test", user_id="u1", http=http, kb=kb)

        with pytest.raises(HttpRequestFailed) as exc_info:
            await cap.from_http(
                "kb-1",
                method="GET",
                url="https://x",
                filename="f.pdf",
                mime_type="application/pdf",
                source_id="ext-1",
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.url == "https://x"
        kb.ingest_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ingest_phase_failure_propagates(self):
        """Exception from kb.ingest_document propagates; fetch_bytes was called."""
        http = MagicMock()
        http.fetch_bytes = AsyncMock(
            return_value={"content": b"data", "status_code": 200, "headers": {}}
        )
        kb = MagicMock()
        kb.ingest_document = AsyncMock(side_effect=Exception("DB error"))

        cap = IngestCapability(plugin_name="test", user_id="u1", http=http, kb=kb)

        with pytest.raises(Exception, match="DB error"):
            await cap.from_http(
                "kb-1",
                method="GET",
                url="https://example.com/doc.pdf",
                filename="doc.pdf",
                mime_type="application/pdf",
                source_id="ext-2",
            )

        http.fetch_bytes.assert_awaited_once()


class TestMakeHostWiring:
    """Tests for make_host ingest-capability wiring and validation."""

    def test_ingest_without_http_and_kb_raises_value_error(self):
        """Requesting ingest alone fails because it needs both http and kb."""
        with pytest.raises(ValueError, match="ingest capability requires http and kb"):
            make_host(
                plugin_name="p",
                user_id="u",
                user_email=None,
                capabilities=["ingest"],
            )

    def test_ingest_without_kb_raises_value_error(self):
        """Requesting ingest+http (but no kb) fails."""
        with pytest.raises(ValueError, match="ingest capability requires http and kb"):
            make_host(
                plugin_name="p",
                user_id="u",
                user_email=None,
                capabilities=["ingest", "http"],
            )

    def test_ingest_with_http_and_kb_succeeds(self):
        """make_host wires an IngestCapability when ingest+http+kb are all declared."""
        h = make_host(
            plugin_name="p",
            user_id="u",
            user_email=None,
            capabilities=["ingest", "http", "kb"],
        )

        ingest = object.__getattribute__(h, "ingest")
        assert ingest is not None
        assert isinstance(ingest, IngestCapability)
