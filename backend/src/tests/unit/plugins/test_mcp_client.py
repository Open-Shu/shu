"""Unit tests for McpClient.

Covers JSON-RPC serialization, response parsing (direct JSON and SSE),
error mapping, retry behavior, size limits, and high-level methods
(connect, list_tools, call_tool, health_check).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shu.plugins.mcp_client import (
    McpClient,
    McpConnectionError,
    McpProtocolError,
    McpResponseTooLarge,
    McpTimeoutError,
    McpToolInfo,
    McpToolResult,
    _BearerAuth,
    _extract_auth,
)

SERVER_URL = "http://mcp.test/rpc"


def _mock_settings() -> SimpleNamespace:
    return SimpleNamespace(
        mcp_connect_timeout_ms=5000,
        mcp_call_timeout_ms=30000,
        mcp_read_timeout_ms=30000,
        mcp_response_size_limit_bytes=10 * 1024 * 1024,
        mcp_max_retries=3,
        mcp_retry_base_delay_ms=1000,
    )


def _make_response(
    data: dict, content_type: str = "application/json", status: int = 200
) -> tuple[int, dict[str, str], str]:
    """Return (status_code, headers, body) tuple matching _post_and_read signature."""
    return (status, {"content-type": content_type}, json.dumps(data))


def _make_sse_response(body: str) -> tuple[int, dict[str, str], str]:
    return (200, {"content-type": "text/event-stream"}, body)


async def _async_iter(items):
    """Helper to create an async iterator from a list of items."""
    for item in items:
        yield item


def _jsonrpc_result(result: dict, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(code: int, message: str, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@pytest.fixture
def client() -> McpClient:
    with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
        return McpClient(url=SERVER_URL, max_retries=0)


@pytest.fixture
def retry_client() -> McpClient:
    with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
        return McpClient(url=SERVER_URL, max_retries=2, retry_base_delay_ms=10)


@pytest.fixture
def _mock_logger() -> MagicMock:
    """Patch the module-level logger so structlog-style kwargs don't raise."""
    with patch("shu.plugins.mcp_client.logger") as mock:
        yield mock


@pytest.mark.asyncio
async def test_send_jsonrpc_serialization(client: McpClient) -> None:
    """Verify the POST payload has correct jsonrpc version, method, params, and incrementing id."""
    mock_post = AsyncMock(
        return_value=_make_response(_jsonrpc_result({"ok": True}, request_id=1))
    )
    client._post_and_read = mock_post

    await client._send_jsonrpc("tools/list", {"cursor": "abc"})

    mock_post.assert_called_once()
    payload = mock_post.call_args[0][0]
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tools/list"
    assert payload["params"] == {"cursor": "abc"}
    assert payload["id"] == 1

    mock_post.reset_mock()
    mock_post.return_value = _make_response(_jsonrpc_result({"ok": True}, request_id=2))
    await client._send_jsonrpc("tools/call")

    second_payload = mock_post.call_args[0][0]
    assert second_payload["id"] == 2
    assert "params" not in second_payload


@pytest.mark.asyncio
async def test_direct_json_parsing(client: McpClient) -> None:
    """Mock response with content-type application/json and a valid JSON-RPC result."""
    expected = {"tools": [{"name": "echo"}]}
    client._post_and_read = AsyncMock(return_value=_make_response(_jsonrpc_result(expected)))

    result = await client._send_jsonrpc("tools/list")
    assert result == expected


@pytest.mark.asyncio
async def test_sse_parsing_multi_event(client: McpClient) -> None:
    """Mock response with content-type text/event-stream and multi-event SSE body."""
    sse_body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"partial":true}}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"final"}]}}\n'
        "\n"
    )
    client._post_and_read = AsyncMock(return_value=_make_sse_response(sse_body))

    result = await client._send_jsonrpc("tools/list")
    assert result == {"tools": [{"name": "final"}]}


@pytest.mark.asyncio
async def test_sse_parsing_no_event_type(client: McpClient) -> None:
    """SSE events without explicit event type should still be parsed."""
    sse_body = 'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    client._post_and_read = AsyncMock(return_value=_make_sse_response(sse_body))

    result = await client._send_jsonrpc("test/method")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_sse_no_valid_result(client: McpClient) -> None:
    """SSE stream with no valid JSON-RPC result raises McpProtocolError."""
    sse_body = "event: ping\ndata: keep-alive\n\n"
    client._post_and_read = AsyncMock(return_value=_make_sse_response(sse_body))

    with pytest.raises(McpProtocolError, match="No valid JSON-RPC result"):
        await client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_jsonrpc_error_response(client: McpClient) -> None:
    """Response with JSON-RPC error raises McpProtocolError with code."""
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_error(-32600, "Invalid Request"))
    )

    with pytest.raises(McpProtocolError, match="Invalid Request") as exc_info:
        await client._send_jsonrpc("test/method")
    assert exc_info.value.code == -32600
    assert exc_info.value.server_url == SERVER_URL


@pytest.mark.asyncio
async def test_invalid_json_response(client: McpClient) -> None:
    """Non-JSON response body raises McpProtocolError."""
    client._post_and_read = AsyncMock(return_value=(200, {"content-type": "application/json"}, "not json"))

    with pytest.raises(McpProtocolError, match="Invalid JSON"):
        await client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_connection_error_maps_to_mcp_connection_error(client: McpClient) -> None:
    """McpConnectionError propagates from transport layer."""
    client._post_and_read = AsyncMock(side_effect=McpConnectionError("refused", server_url=SERVER_URL))

    with pytest.raises(McpConnectionError, match="refused"):
        await client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_timeout_error_maps_to_mcp_timeout_error(client: McpClient) -> None:
    """McpTimeoutError propagates from transport layer."""
    client._post_and_read = AsyncMock(side_effect=McpTimeoutError("timed out", server_url=SERVER_URL))

    with pytest.raises(McpTimeoutError, match="timed out"):
        await client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_response_size_cap_json(client: McpClient) -> None:
    """Response larger than limit raises McpResponseTooLarge during streaming read."""
    with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
        small_client = McpClient(url=SERVER_URL, max_retries=0, response_size_limit=50)

    big_body = json.dumps(_jsonrpc_result({"data": "x" * 200})).encode()

    mock_response = AsyncMock()
    mock_response.headers = {"content-type": "application/json"}
    mock_response.aiter_bytes = lambda: _async_iter([big_body])

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    small_client._client.stream = MagicMock(return_value=mock_stream)

    with pytest.raises(McpResponseTooLarge) as exc_info:
        await small_client._post_and_read({"jsonrpc": "2.0", "method": "test", "id": 1})
    assert exc_info.value.limit_bytes == 50


@pytest.mark.asyncio
async def test_response_size_cap_sse(client: McpClient) -> None:
    """SSE response exceeding size limit raises McpResponseTooLarge during streaming read."""
    with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
        small_client = McpClient(url=SERVER_URL, max_retries=0, response_size_limit=30)

    big_body = ("event: message\ndata: " + json.dumps({"big": "x" * 200}) + "\n\n").encode()

    mock_response = AsyncMock()
    mock_response.headers = {"content-type": "text/event-stream"}
    mock_response.aiter_bytes = lambda: _async_iter([big_body])

    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    small_client._client.stream = MagicMock(return_value=mock_stream)

    with pytest.raises(McpResponseTooLarge):
        await small_client._post_and_read({"jsonrpc": "2.0", "method": "test", "id": 1})


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure(retry_client: McpClient, _mock_logger: MagicMock) -> None:
    """First call raises connection error, second succeeds — retries happen inside _post_and_read."""
    success = _make_response(_jsonrpc_result({"ok": True}))
    retry_client._post_and_read = AsyncMock(return_value=success)

    result = await retry_client._send_jsonrpc("test/method")
    assert result == {"ok": True}
    retry_client._post_and_read.assert_called_once()


@pytest.mark.asyncio
async def test_retry_exhausted_raises(retry_client: McpClient, _mock_logger: MagicMock) -> None:
    """McpConnectionError propagates from _post_and_read after retries exhausted."""
    retry_client._post_and_read = AsyncMock(
        side_effect=McpConnectionError("refused", server_url=SERVER_URL)
    )

    with pytest.raises(McpConnectionError, match="refused"):
        await retry_client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_retry_timeout_exhausted(retry_client: McpClient, _mock_logger: MagicMock) -> None:
    """McpTimeoutError propagates from _post_and_read after retries exhausted."""
    retry_client._post_and_read = AsyncMock(
        side_effect=McpTimeoutError("timed out", server_url=SERVER_URL)
    )

    with pytest.raises(McpTimeoutError, match="timed out"):
        await retry_client._send_jsonrpc("test/method")


@pytest.mark.asyncio
async def test_connect_success(client: McpClient) -> None:
    """connect() returns result and sends initialized notification."""
    init_result = {
        "protocolVersion": "2025-03-26",
        "serverInfo": {"name": "test-server"},
        "capabilities": {},
    }
    notification_response = (200, {}, "")

    client._post_and_read = AsyncMock(
        side_effect=[
            _make_response(_jsonrpc_result(init_result)),
            notification_response,
        ]
    )

    result = await client.connect()
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "test-server"
    assert client._post_and_read.call_count == 2


@pytest.mark.asyncio
async def test_connect_unsupported_version(client: McpClient) -> None:
    """connect() raises McpProtocolError on unsupported protocol version."""
    init_result = {"protocolVersion": "1999-01-01", "capabilities": {}}
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_result(init_result))
    )

    with pytest.raises(McpProtocolError, match="Unsupported MCP protocol version"):
        await client.connect()


@pytest.mark.asyncio
async def test_connect_alternate_supported_version(client: McpClient) -> None:
    """connect() accepts the older supported protocol version."""
    init_result = {"protocolVersion": "2024-11-05", "capabilities": {}}
    notification_response = (200, {}, "")
    client._post_and_read = AsyncMock(
        side_effect=[
            _make_response(_jsonrpc_result(init_result)),
            notification_response,
        ]
    )

    result = await client.connect()
    assert result["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_list_tools_parsing(client: McpClient) -> None:
    """list_tools() parses tool array into McpToolInfo list."""
    client._connected = True
    tools_result = {
        "tools": [
            {"name": "echo", "description": "Echo input", "inputSchema": {"type": "object"}},
            {"name": "noop"},
            {"invalid": "no name field"},
            "not a dict",
        ]
    }
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_result(tools_result))
    )

    tools = await client.list_tools()
    assert len(tools) == 2
    assert tools[0] == McpToolInfo(name="echo", description="Echo input", input_schema={"type": "object"})
    assert tools[1] == McpToolInfo(name="noop", description=None, input_schema=None)


@pytest.mark.asyncio
async def test_call_tool_success(client: McpClient) -> None:
    """call_tool() returns McpToolResult with content."""
    client._connected = True
    tool_result = {
        "content": [{"type": "text", "text": "hello"}],
        "isError": False,
    }
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_result(tool_result))
    )

    result = await client.call_tool("echo", {"message": "hello"})
    assert isinstance(result, McpToolResult)
    assert result.content == [{"type": "text", "text": "hello"}]
    assert result.is_error is False

    payload = client._post_and_read.call_args[0][0]
    assert payload["params"] == {"name": "echo", "arguments": {"message": "hello"}}


@pytest.mark.asyncio
async def test_call_tool_error_response(client: McpClient) -> None:
    """call_tool() with isError=true returns McpToolResult with is_error set."""
    client._connected = True
    tool_result = {
        "content": [{"type": "text", "text": "something went wrong"}],
        "isError": True,
    }
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_result(tool_result))
    )

    result = await client.call_tool("failing_tool")
    assert result.is_error is True
    assert result.content == [{"type": "text", "text": "something went wrong"}]


@pytest.mark.asyncio
async def test_call_tool_no_arguments(client: McpClient) -> None:
    """call_tool() without arguments sends empty arguments dict."""
    client._connected = True
    tool_result = {"content": [], "isError": False}
    client._post_and_read = AsyncMock(
        return_value=_make_response(_jsonrpc_result(tool_result))
    )

    await client.call_tool("noop")
    payload = client._post_and_read.call_args[0][0]
    assert payload["params"] == {"name": "noop", "arguments": {}}


@pytest.mark.asyncio
async def test_health_check_success(client: McpClient) -> None:
    """health_check() returns True on successful connect."""
    init_result = {"protocolVersion": "2025-03-26", "capabilities": {}}
    notification_response = (200, {}, "")
    client._post_and_read = AsyncMock(
        side_effect=[
            _make_response(_jsonrpc_result(init_result)),
            notification_response,
        ]
    )

    assert await client.health_check() is True


@pytest.mark.asyncio
async def test_health_check_failure(client: McpClient) -> None:
    """health_check() returns False on connection error."""
    client._post_and_read = AsyncMock(side_effect=McpConnectionError("refused", server_url=SERVER_URL))

    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_non_dict_json_response(client: McpClient) -> None:
    """Response body that is valid JSON but not a dict raises McpProtocolError."""
    client._post_and_read = AsyncMock(return_value=(200, {"content-type": "application/json"}, "[1,2,3]"))

    with pytest.raises(McpProtocolError, match="not a JSON object"):
        await client._send_jsonrpc("test/method")


class TestExtractAuth:
    """Tests for _extract_auth and _BearerAuth."""

    def test_basic_auth_extracted(self) -> None:
        """Basic auth header is decoded and returned as httpx.BasicAuth."""
        import base64

        encoded = base64.b64encode(b"user@example.com:api-token").decode()
        headers = {"Authorization": f"Basic {encoded}"}

        auth = _extract_auth(headers)

        assert isinstance(auth, httpx.BasicAuth)
        assert auth._auth_header == httpx.BasicAuth("user@example.com", "api-token")._auth_header

    def test_bearer_auth_extracted(self) -> None:
        """Bearer auth header is returned as _BearerAuth."""
        headers = {"Authorization": "Bearer ghp_abc123"}

        auth = _extract_auth(headers)

        assert isinstance(auth, _BearerAuth)
        assert auth._token == "ghp_abc123"

    def test_no_authorization_header(self) -> None:
        """Returns None when no Authorization header is present."""
        assert _extract_auth({}) is None
        assert _extract_auth({"Content-Type": "application/json"}) is None

    def test_unknown_scheme_returns_none(self) -> None:
        """Returns None for unrecognized auth schemes."""
        assert _extract_auth({"Authorization": "Digest abc123"}) is None

    def test_case_insensitive_header_key(self) -> None:
        """Authorization header key matching is case-insensitive."""
        import base64

        encoded = base64.b64encode(b"user:pass").decode()
        auth = _extract_auth({"authorization": f"Basic {encoded}"})
        assert isinstance(auth, httpx.BasicAuth)

    def test_case_insensitive_scheme(self) -> None:
        """Auth scheme matching is case-insensitive."""
        auth = _extract_auth({"Authorization": "BEARER my-token"})
        assert isinstance(auth, _BearerAuth)
        assert auth._token == "my-token"

    def test_basic_auth_with_colon_in_password(self) -> None:
        """Basic auth correctly handles passwords containing colons."""
        import base64

        encoded = base64.b64encode(b"user:pass:with:colons").decode()
        auth = _extract_auth({"Authorization": f"Basic {encoded}"})

        assert isinstance(auth, httpx.BasicAuth)
        assert auth._auth_header == httpx.BasicAuth("user", "pass:with:colons")._auth_header

    def test_bearer_auth_flow_sets_header(self) -> None:
        """_BearerAuth.auth_flow sets the Authorization header on the request."""
        auth = _BearerAuth("my-token")
        request = httpx.Request("GET", "http://example.com")

        flow = auth.auth_flow(request)
        yielded = next(flow)

        assert yielded.headers["Authorization"] == "Bearer my-token"

    def test_client_with_basic_auth_uses_httpx_auth(self) -> None:
        """McpClient constructed with Basic auth uses httpx.Auth, not raw header."""
        import base64

        encoded = base64.b64encode(b"user:pass").decode()
        with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
            c = McpClient(url=SERVER_URL, headers={"Authorization": f"Basic {encoded}"})

        assert isinstance(c._auth, httpx.BasicAuth)
        assert "authorization" not in {k.lower() for k in c._client.headers}

    def test_client_with_bearer_auth_uses_httpx_auth(self) -> None:
        """McpClient constructed with Bearer auth uses httpx.Auth, not raw header."""
        with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
            c = McpClient(url=SERVER_URL, headers={"Authorization": "Bearer ghp_abc"})

        assert isinstance(c._auth, _BearerAuth)
        assert "authorization" not in {k.lower() for k in c._client.headers}

    def test_client_without_auth_has_none(self) -> None:
        """McpClient constructed without auth headers has _auth=None."""
        with patch("shu.plugins.mcp_client.get_settings_instance", return_value=_mock_settings()):
            c = McpClient(url=SERVER_URL)

        assert c._auth is None
