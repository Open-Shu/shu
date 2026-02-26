"""
Unit tests for Executor._validate None-stripping behaviour.

Tests cover:
- Optional params sent as None/null (OLLAMA) are silently stripped
- Required fields that are missing still raise HTTP 422
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

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

from fastapi import HTTPException

from shu.plugins.executor import Executor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(schema: dict) -> MagicMock:
    """Return a minimal plugin stub whose get_schema() returns *schema*."""
    plugin = MagicMock()
    plugin.get_schema.return_value = schema
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

    result = executor._validate(plugin, {"op": "list", "query_filter": None})

    assert result == {"op": "list"}, (
        "query_filter=None should be stripped; returned dict must not contain it"
    )


def test_validate_required_field_still_enforced():
    """Missing required field raises HTTP 422 even when other params are None."""
    executor = _make_executor()
    plugin = _make_plugin(_SCHEMA)

    with pytest.raises(HTTPException) as exc_info:
        executor._validate(plugin, {"query_filter": None})

    assert exc_info.value.status_code == 422, (
        "Missing required field 'op' must raise HTTP 422"
    )
