"""Cookie-cutter test file — copy this alongside plugin.py when starting a new plugin.

This file gives you three things for free:
1. ``test_contract()`` — runs the full SDK contract check against your plugin.
   Keep this test as-is; it will catch manifest errors, schema violations, and
   op cross-reference issues before you ever deploy to Shu.

2. ``test_echo_op()`` — shows the minimal FakeHostBuilder pattern (no HTTP, no secrets).

3. ``test_fetch_op_*`` — shows how to inject a secret and stub HTTP responses so
   your plugin's external calls are fully testable without hitting the network.

To add your own runtime tests:
  - Copy one of the patterns below
  - Use ``FakeHostBuilder`` to inject secrets, mock HTTP responses, or raise errors
  - Call ``plugin.execute(params, context, host)`` and assert on the result
"""

from __future__ import annotations

import pytest

from shu_plugin_sdk.contracts import assert_plugin_contract
from shu_plugin_sdk.testing import FakeHostBuilder, HttpRequestFailed

from _cookiecutter.manifest import PLUGIN_MANIFEST
from _cookiecutter.plugin import EchoPlugin


# ---------------------------------------------------------------------------
# Contract gate — do not modify.
# This test runs the full SDK validation pipeline against EchoPlugin.
# It will fail if your manifest, schemas, or op cross-references are invalid.
# ---------------------------------------------------------------------------

def test_contract() -> None:
    """Assert that EchoPlugin satisfies the full Shu plugin contract."""
    assert_plugin_contract(EchoPlugin, manifest=PLUGIN_MANIFEST)


# ---------------------------------------------------------------------------
# Runtime tests — replace / extend with your own.
# ---------------------------------------------------------------------------

# Minimal context shim — replace with a real ExecuteContext if you need
# user_id or agent_key fields in your plugin logic.
_CTX = type("Ctx", (), {"user_id": "test_user", "agent_key": None})()


@pytest.mark.asyncio
async def test_echo_op() -> None:
    """EchoPlugin returns the echoed message on a successful 'echo' op.

    Demonstrates the simplest FakeHostBuilder usage: no secrets, no HTTP.
    """
    plugin = EchoPlugin()
    host = FakeHostBuilder().build()

    result = await plugin.execute({"op": "echo", "message": "hello"}, _CTX, host)

    assert result.status == "success"
    assert result.data["echo"] == "hello"


@pytest.mark.asyncio
async def test_fetch_op_with_secret_and_http() -> None:
    """EchoPlugin makes an authenticated HTTP call for the 'fetch' op.

    Demonstrates the full FakeHostBuilder pattern:
      1. Inject a secret with with_secret()
      2. Stub an HTTP response with with_http_response()
      3. Assert on the result
    """
    plugin = EchoPlugin()
    host = (
        FakeHostBuilder()
        .with_secret("api_key", "test_token_123")
        .with_http_response(
            "GET",
            "https://api.example.com/data",
            {"status_code": 200, "headers": {}, "body": {"id": 42}},
        )
        .build()
    )

    result = await plugin.execute(
        {"op": "fetch", "url": "https://api.example.com/data"}, _CTX, host
    )

    assert result.status == "success"
    assert result.data["status_code"] == 200
    assert result.data["body"] == {"id": 42}


@pytest.mark.asyncio
async def test_fetch_op_http_error() -> None:
    """EchoPlugin propagates HttpRequestFailed when the HTTP call fails.

    Demonstrates with_http_error() to simulate rate-limiting or server errors.
    """
    plugin = EchoPlugin()
    host = (
        FakeHostBuilder()
        .with_http_error("GET", "https://api.example.com/data", 429)
        .build()
    )

    with pytest.raises(HttpRequestFailed) as exc_info:
        await plugin.execute(
            {"op": "fetch", "url": "https://api.example.com/data"}, _CTX, host
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.is_retryable is True
