"""Tests for ApiPluginAdapter.

Covers HTTP request construction (URL building, query params, body encoding,
auth header injection), parameter validation, response assembly, error
handling (timeouts, connection errors, HTTP 4xx/5xx), response size limits,
and 429 retry logic.

All tests use a FakeConnection and httpx.MockTransport to avoid real network calls.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from shu.plugins.api_adapter import ApiPluginAdapter, ApiToolResult
from shu.plugins.base import PluginResult


class FakeConnection:
    """Minimal stand-in for ApiServerConnection."""

    def __init__(
        self,
        name: str = "test-api",
        tool_configs: dict[str, Any] | None = None,
        discovered_tools: list[dict[str, Any]] | None = None,
        auth_config: dict[str, Any] | None = None,
        base_url: str = "https://api.example.com",
        url: str = "https://api.example.com",
        response_size_limit_bytes: int | None = None,
        timeouts: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.tool_configs = tool_configs
        self.discovered_tools = discovered_tools or []
        self.auth_config = auth_config
        self.base_url = base_url
        self.url = url
        self.response_size_limit_bytes = response_size_limit_bytes
        self.timeouts = timeouts
        self.spec_type = "openapi"


def _make_tool(
    name: str = "get_users",
    method: str = "GET",
    path: str = "/users",
    path_params: list[str] | None = None,
    query_params: list[str] | None = None,
    has_body: bool = False,
    content_type: str | None = None,
    input_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a discovered tool dict with operation metadata."""
    tool: dict[str, Any] = {
        "name": name,
        "method": method,
        "path": path,
        "path_params": path_params or [],
        "query_params": query_params or [],
        "has_body": has_body,
    }
    if content_type is not None:
        tool["content_type"] = content_type
    if input_schema is not None:
        tool["inputSchema"] = input_schema
    return tool


def _mock_transport(
    status_code: int = 200,
    body: dict[str, Any] | str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """Create an httpx.MockTransport returning a fixed response."""
    resp_headers = dict(headers or {})
    if isinstance(body, dict):
        content = json.dumps(body).encode()
        resp_headers.setdefault("content-type", "application/json")
    elif isinstance(body, str):
        content = body.encode()
    else:
        content = b""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=content, headers=resp_headers)

    return httpx.MockTransport(handler)


def _make_adapter(
    discovered_tools: list[dict[str, Any]] | None = None,
    tool_configs: dict[str, Any] | None = None,
    auth_config: dict[str, Any] | None = None,
    base_url: str = "https://api.example.com",
    response_size_limit_bytes: int | None = None,
    timeouts: dict[str, Any] | None = None,
    transport: httpx.MockTransport | None = None,
    credential: str | None = None,
) -> ApiPluginAdapter:
    """Build an adapter with a FakeConnection and optional mock transport."""
    conn = FakeConnection(
        discovered_tools=discovered_tools,
        tool_configs=tool_configs,
        auth_config=auth_config,
        base_url=base_url,
        response_size_limit_bytes=response_size_limit_bytes,
        timeouts=timeouts,
    )
    client = None
    if transport is not None:
        client = httpx.AsyncClient(transport=transport)
    adapter = ApiPluginAdapter(conn, http_client=client, credential=credential)  # type: ignore[arg-type]
    return adapter


class TestCallToolSuccess:
    """Successful _call_tool invocations with various HTTP methods and param types."""

    @pytest.mark.asyncio
    async def test_get_request_returns_json(self) -> None:
        """A simple GET returns parsed JSON wrapped in ApiToolResult."""
        tool = _make_tool(name="list_users", method="GET", path="/users")
        transport = _mock_transport(body={"users": [{"id": 1}]})
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"list_users": {"enabled": True}},
            transport=transport,
        )

        result = await adapter._call_tool("list_users", {})

        assert isinstance(result, ApiToolResult)
        assert result.content == {"users": [{"id": 1}]}

    @pytest.mark.asyncio
    async def test_post_request_sends_body(self) -> None:
        """POST with has_body sends JSON body and returns response."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"created": True})

        tool = _make_tool(
            name="create_user", method="POST", path="/users", has_body=True,
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"create_user": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        result = await adapter._call_tool("create_user", {"name": "Alice", "email": "a@b.com"})

        assert isinstance(result, ApiToolResult)
        assert result.content == {"created": True}
        assert len(captured_requests) == 1
        sent_body = json.loads(captured_requests[0].content)
        assert sent_body == {"name": "Alice", "email": "a@b.com"}

    @pytest.mark.asyncio
    async def test_path_param_substitution(self) -> None:
        """Path parameters are substituted into the URL template."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"id": 42})

        tool = _make_tool(
            name="get_user", method="GET", path="/users/{user_id}",
            path_params=["user_id"],
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_user": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("get_user", {"path_user_id": "42"})

        assert str(captured_requests[0].url.path) == "/users/42"

    @pytest.mark.asyncio
    async def test_query_params_added_to_url(self) -> None:
        """Query parameters are passed as URL query string."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"results": []})

        tool = _make_tool(
            name="search", method="GET", path="/search",
            query_params=["q", "limit"],
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"search": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("search", {"q": "test", "limit": "10"})

        url = captured_requests[0].url
        assert url.params["q"] == "test"
        assert url.params["limit"] == "10"

    @pytest.mark.asyncio
    async def test_internal_keys_stripped(self) -> None:
        """Internal keys (op, kb_id, __schedule_id, etc.) are stripped before building request."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        tool = _make_tool(name="do_thing", method="POST", path="/thing", has_body=True)
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"do_thing": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("do_thing", {
            "op": "do_thing",
            "kb_id": "kb1",
            "__schedule_id": "sched-1",
            "real_param": "value",
        })

        sent_body = json.loads(captured_requests[0].content)
        assert sent_body == {"real_param": "value"}

    @pytest.mark.asyncio
    async def test_unknown_operation_returns_error(self) -> None:
        """Calling a nonexistent operation returns PluginResult error."""
        adapter = _make_adapter(discovered_tools=[], tool_configs={})

        result = await adapter._call_tool("nonexistent", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "unknown_operation"


class TestAuthHeaderInjection:
    """Auth header injection based on connection auth_config."""

    @pytest.mark.asyncio
    async def test_bearer_auth(self) -> None:
        """Bearer auth injects Authorization: Bearer <token>."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            auth_config={"type": "header", "name": "Authorization", "prefix": "Bearer "},
            credential="my-token-123",
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("get_users", {})

        assert captured_requests[0].headers["authorization"] == "Bearer my-token-123"

    @pytest.mark.asyncio
    async def test_query_auth(self) -> None:
        """Query auth injects credential as a query parameter."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            auth_config={"type": "query", "name": "api_key", "prefix": ""},
            credential="secret-key-123",
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("get_users", {})

        assert "api_key=secret-key-123" in str(captured_requests[0].url)

    @pytest.mark.asyncio
    async def test_custom_header_auth(self) -> None:
        """Header auth with custom header name and no prefix."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            auth_config={"type": "header", "name": "X-Api-Key", "prefix": ""},
            credential="secret-key",
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("get_users", {})

        assert captured_requests[0].headers["x-api-key"] == "secret-key"

    @pytest.mark.asyncio
    async def test_no_auth_config_sends_no_auth_header(self) -> None:
        """Without auth_config, no Authorization header is sent."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("get_users", {})

        assert "authorization" not in captured_requests[0].headers


class TestBodyEncoding:
    """Body encoding varies by content_type."""

    @pytest.mark.asyncio
    async def test_json_body_default(self) -> None:
        """Default content type sends JSON body."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        tool = _make_tool(name="create", method="POST", path="/items", has_body=True)
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"create": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("create", {"field": "value"})

        content_type = captured_requests[0].headers.get("content-type", "")
        assert "application/json" in content_type

    @pytest.mark.asyncio
    async def test_form_urlencoded_body(self) -> None:
        """content_type 'application/x-www-form-urlencoded' sends form data."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        tool = _make_tool(
            name="submit_form", method="POST", path="/form",
            has_body=True, content_type="application/x-www-form-urlencoded",
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"submit_form": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        await adapter._call_tool("submit_form", {"username": "alice"})

        content_type = captured_requests[0].headers.get("content-type", "")
        assert "application/x-www-form-urlencoded" in content_type


class TestErrorHandling:
    """HTTP error responses and transport failures."""

    @pytest.mark.asyncio
    async def test_timeout_returns_plugin_result_error(self) -> None:
        """Timeout exception returns PluginResult with code 'timeout'."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        result = await adapter._call_tool("get_users", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "timeout"

    @pytest.mark.asyncio
    async def test_connection_error_returns_plugin_result_error(self) -> None:
        """ConnectError returns PluginResult with code 'connection_error'."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=httpx.MockTransport(handler),
        )

        result = await adapter._call_tool("get_users", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "connection_error"

    @pytest.mark.asyncio
    async def test_4xx_returns_error_with_body(self) -> None:
        """HTTP 404 returns PluginResult error with status code and body excerpt."""
        transport = _mock_transport(status_code=404, body={"detail": "Not found"})
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=transport,
        )

        result = await adapter._call_tool("get_users", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "http_404"
        assert "404" in result.error["message"]

    @pytest.mark.asyncio
    async def test_5xx_returns_error_with_body(self) -> None:
        """HTTP 500 returns PluginResult error with status code."""
        transport = _mock_transport(status_code=500, body="Internal Server Error")
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=transport,
        )

        result = await adapter._call_tool("get_users", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "http_500"


class TestRetryOn429:
    """429 retry behavior with Retry-After header."""

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self) -> None:
        """First request gets 429, retry succeeds with 200."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"ok": True})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=httpx.MockTransport(handler),
            timeouts={"read_ms": 60000},
        )

        with patch("shu.plugins.api_adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter._call_tool("get_users", {})

        assert isinstance(result, ApiToolResult)
        assert result.content == {"ok": True}

    @pytest.mark.asyncio
    async def test_429_max_retries_exceeded(self) -> None:
        """All retries return 429 — eventually gives up with rate_limited error."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "0"})

        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=httpx.MockTransport(handler),
            timeouts={"read_ms": 60000},
        )

        with patch("shu.plugins.api_adapter.asyncio.sleep", new_callable=AsyncMock):
            result = await adapter._call_tool("get_users", {})

        assert isinstance(result, PluginResult)
        assert result.status == "error"
        assert result.error["code"] == "rate_limited"


class TestResponseSizeLimit:
    """Response body size enforcement via streaming."""

    @pytest.mark.asyncio
    async def test_response_exceeding_size_limit_raises(self) -> None:
        """A response larger than response_size_limit_bytes raises DecodingError from _read_response_body."""
        large_body = {"data": "x" * 1000}
        transport = _mock_transport(body=large_body)
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=transport,
            response_size_limit_bytes=50,
        )

        with pytest.raises(httpx.DecodingError, match="size limit"):
            await adapter._call_tool("get_users", {})

    @pytest.mark.asyncio
    async def test_response_within_limit_succeeds(self) -> None:
        """A response within the size limit returns normally."""
        small_body = {"ok": True}
        transport = _mock_transport(body=small_body)
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
            transport=transport,
            response_size_limit_bytes=10000,
        )

        result = await adapter._call_tool("get_users", {})

        assert isinstance(result, ApiToolResult)
        assert result.content == {"ok": True}


class TestValidateParams:
    """Parameter validation based on inputSchema required fields."""

    def test_missing_required_param_returns_error_message(self) -> None:
        """Missing required parameters produce an error message."""
        tool = _make_tool(
            input_schema={"required": ["user_id", "name"], "properties": {}},
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )

        error = adapter._validate_params("get_users", {"name": "Alice"})

        assert error is not None
        assert "user_id" in error

    def test_all_required_params_present_returns_none(self) -> None:
        """All required parameters present passes validation."""
        tool = _make_tool(
            input_schema={"required": ["user_id"], "properties": {}},
        )
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )

        error = adapter._validate_params("get_users", {"user_id": "42"})

        assert error is None

    def test_no_input_schema_passes(self) -> None:
        """A tool without inputSchema passes validation."""
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )

        error = adapter._validate_params("get_users", {})

        assert error is None


class TestAssembleResponseData:
    """Converting ApiToolResult into plain dicts."""

    def test_dict_content_returned_directly(self) -> None:
        """Dict content passes through unchanged."""
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )
        raw = ApiToolResult(content={"users": [1, 2]})

        result = adapter._assemble_response_data(raw)

        assert result == {"users": [1, 2]}

    def test_string_content_wrapped_in_text(self) -> None:
        """String content is wrapped as {'text': ...}."""
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )
        raw = ApiToolResult(content="plain text response")

        result = adapter._assemble_response_data(raw)

        assert result == {"text": "plain text response"}

    def test_other_content_wrapped_in_data(self) -> None:
        """Non-dict, non-string content is wrapped as {'data': ...}."""
        tool = _make_tool()
        adapter = _make_adapter(
            discovered_tools=[tool],
            tool_configs={"get_users": {"enabled": True}},
        )
        raw = ApiToolResult(content=[1, 2, 3])

        result = adapter._assemble_response_data(raw)

        assert result == {"data": [1, 2, 3]}


class TestBuildBodyKwargs:
    """Unit tests for _build_body_kwargs selecting correct httpx encoding."""

    def test_none_body_returns_empty(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._build_body_kwargs(None, None)

        assert result == {}

    def test_json_default(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._build_body_kwargs({"a": 1}, None)

        assert result == {"json": {"a": 1}}

    def test_form_urlencoded(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._build_body_kwargs({"a": 1}, "application/x-www-form-urlencoded")

        assert result == {"data": {"a": 1}}

    def test_multipart(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._build_body_kwargs({"file": "data"}, "multipart/form-data")

        assert result == {"files": {"file": "data"}}


class TestParseResponse:
    """Unit tests for _parse_response fallback behavior."""

    def test_json_dict_returned_as_is(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._parse_response('{"key": "value"}')

        assert result == {"key": "value"}

    def test_json_array_wrapped_in_data(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._parse_response('[1, 2, 3]')

        assert result == {"data": [1, 2, 3]}

    def test_invalid_json_wrapped_in_text(self) -> None:
        tool = _make_tool()
        adapter = _make_adapter(discovered_tools=[tool])

        result = adapter._parse_response("not json at all")

        assert result == {"text": "not json at all"}
