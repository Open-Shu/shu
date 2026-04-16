"""
Unit tests for Executor.

Tests cover:
- Optional params sent as None/null (OLLAMA) are silently stripped
- Required fields that are missing still raise HTTP 422
- execute() dispatches to SandboxLauncher with correct arguments
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set required environment variables BEFORE any shu imports
os.environ.setdefault("SHU_DATABASE_URL", "test_db_url")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret")

# ---------------------------------------------------------------------------
# Break the circular import chain before importing shu.plugins.executor.
#
# executor.py imports from .host.exceptions and .host.host_builder, which
# cascade into a circular dependency through the services layer.  Since
# _validate does not use any host capability at runtime, we pre-register
# lightweight mock modules so the import resolves cleanly.
# ---------------------------------------------------------------------------
if "shu.plugins.host.exceptions" not in sys.modules:

    class _FakeHttpRequestFailed(Exception):
        pass

    _exc_mod = MagicMock()
    _exc_mod.HttpRequestFailed = _FakeHttpRequestFailed
    _exc_mod.CapabilityDenied = Exception
    _exc_mod.EgressDenied = Exception
    sys.modules["shu.plugins.host.exceptions"] = _exc_mod

if "shu.plugins.host.host_builder" not in sys.modules:
    _builder_mod = MagicMock()
    _builder_mod.make_host = MagicMock()
    _builder_mod.HostContext = MagicMock()
    sys.modules["shu.plugins.host.host_builder"] = _builder_mod

if "shu.plugins.host" not in sys.modules:
    _host_mod = MagicMock()
    sys.modules["shu.plugins.host"] = _host_mod

# Also mock the services.policy_engine path which creates a second circular
# import chain: executor -> policy_engine -> services/__init__ -> chat_service
# -> llm -> plugin_execution -> executor.
if "shu.services.policy_engine" not in sys.modules:
    _pe_mod = MagicMock()
    sys.modules["shu.services.policy_engine"] = _pe_mod

# Stub the sandbox package so `from .sandbox.launcher import SandboxLauncher`
# in executor.py resolves without pulling in real subprocess/logging deps.
if "shu.plugins.sandbox" not in sys.modules:
    sys.modules["shu.plugins.sandbox"] = MagicMock()
if "shu.plugins.sandbox.launcher" not in sys.modules:
    sys.modules["shu.plugins.sandbox.launcher"] = MagicMock()

from fastapi import HTTPException

from shu.plugins.executor import Executor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(schema: dict) -> MagicMock:
    """Return a minimal plugin stub whose get_schema_for_op() returns *schema*."""
    plugin = MagicMock()
    plugin.name = "test-plugin"
    plugin.get_schema_for_op.return_value = schema
    return plugin


def _make_executor() -> Executor:
    """Return an Executor instance with rate limiting disabled."""
    settings = MagicMock()
    settings.enable_api_rate_limiting = False
    return Executor(settings=settings)


# Schema used by both tests:
# - "op" is required
# - "query_filter" is optional (type allows null)
_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string"},
        "query_filter": {"type": ["string", "null"]},
    },
    "required": ["op"],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_validate_strips_none_params():
    """None values for optional params are stripped; plugin receives clean dict."""
    executor = _make_executor()
    plugin = _make_plugin(_SCHEMA)

    result = executor._validate(plugin, {"op": "list", "query_filter": None}, "list")

    assert result == {"op": "list"}, (
        "query_filter=None should be stripped; returned dict must not contain it"
    )


def test_validate_required_field_still_enforced():
    """Missing required field raises HTTP 422 even when other params are None."""
    executor = _make_executor()
    plugin = _make_plugin(_SCHEMA)

    with pytest.raises(HTTPException) as exc_info:
        executor._validate(plugin, {"query_filter": None}, "list")

    assert exc_info.value.status_code == 422, (
        "Missing required field 'op' must raise HTTP 422"
    )


@pytest.mark.asyncio
async def test_execute_dispatches_to_sandbox_launcher():
    """execute() constructs a SandboxLauncher and calls launcher.run()."""
    plugin = MagicMock()
    plugin.name = "test-plugin"
    plugin.version = "1.0.0"
    plugin.__module__ = "plugins.test_plugin.plugin"
    plugin._capabilities = ["http"]
    plugin._op_auth = None
    plugin.get_schema_for_op.return_value = None
    plugin.get_schema.return_value = None
    plugin.get_output_schema.return_value = None

    mock_result = MagicMock()
    mock_result.status = "success"
    mock_result.data = {"test": True}

    mock_launcher_instance = MagicMock()
    mock_launcher_instance.run = AsyncMock(return_value=mock_result)

    mock_settings = MagicMock()
    mock_settings.plugin_sandbox_timeout_seconds = 30
    mock_settings.enable_api_rate_limiting = False
    mock_settings.plugin_exec_output_max_bytes = 1024 * 1024
    mock_settings.plugin_quota_daily_requests_default = 0
    mock_settings.plugin_quota_monthly_requests_default = 0

    mock_db_session = AsyncMock()
    mock_policy_check = AsyncMock(return_value=True)

    with (
        patch(
            "shu.plugins.executor.SandboxLauncher",
            return_value=mock_launcher_instance,
        ) as mock_launcher_cls,
        patch(
            "shu.plugins.executor.get_settings_instance",
            return_value=mock_settings,
        ),
        patch("shu.plugins.executor.make_host", return_value=MagicMock()),
        patch("shu.plugins.executor.POLICY_CACHE") as mock_policy_cache,
    ):
        mock_policy_cache.check = mock_policy_check

        executor = Executor(settings=mock_settings)
        result = await executor.execute(
            plugin=plugin,
            user_id="test-user",
            user_email="test@test.com",
            agent_key=None,
            params={"key": "value"},
            db_session=mock_db_session,
        )

    assert result.status == "success"
    mock_launcher_cls.assert_called_once_with(
        timeout_seconds=30,
        settings=mock_settings,
    )
    mock_launcher_instance.run.assert_awaited_once()
    call_kwargs = mock_launcher_instance.run.call_args.kwargs
    assert call_kwargs["plugin_module"] == "plugins.test_plugin.plugin"
    assert call_kwargs["plugin_class"] == type(plugin).__name__
    assert call_kwargs["vparams"] == {"key": "value"}
    assert call_kwargs["user_id"] == "test-user"
    assert call_kwargs["user_email"] == "test@test.com"
    assert call_kwargs["capabilities"] == ["http"]
