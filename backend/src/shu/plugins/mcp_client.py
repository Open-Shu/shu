"""MCP client for Streamable HTTP transport.

Connects to external MCP servers over HTTP + SSE, implementing the
JSON-RPC based MCP protocol for tool discovery and invocation.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from shu.core.config import get_settings_instance
from shu.core.logging import get_logger

logger = get_logger(__name__)


class McpError(Exception):
    """Base exception for MCP client errors."""

    def __init__(self, message: str, server_url: str | None = None) -> None:
        self.server_url = server_url
        super().__init__(message)


class McpConnectionError(McpError):
    """Raised when the MCP server is unreachable."""


class McpTimeoutError(McpError):
    """Raised when an MCP operation exceeds the configured timeout."""


class McpProtocolError(McpError):
    """Raised on MCP protocol violations (version mismatch, invalid JSON-RPC)."""

    def __init__(self, message: str, server_url: str | None = None, code: int | None = None) -> None:
        self.code = code
        super().__init__(message, server_url)


class McpResponseTooLarge(McpError):
    """Raised when the server response exceeds the configured size cap."""

    def __init__(self, message: str, server_url: str | None = None, limit_bytes: int | None = None) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(message, server_url)


@dataclass(frozen=True)
class McpToolInfo:
    """Describes an MCP tool discovered from a server."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpToolResult:
    """Result from calling an MCP tool."""

    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False


MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26"}


class McpClient:
    """Async MCP client using Streamable HTTP transport.

    Sends JSON-RPC requests via HTTP POST and handles both direct JSON
    responses and SSE-streamed responses.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeouts: dict[str, int] | None = None,
        response_size_limit: int | None = None,
        max_retries: int | None = None,
        retry_base_delay_ms: int | None = None,
    ) -> None:
        settings = get_settings_instance()
        self._url = url
        self._headers = headers or {}

        allowlist = getattr(settings, "http_egress_allowlist", None)
        if allowlist:
            hostname = (urllib.parse.urlparse(url).hostname or "").lower()
            allowed = any(
                hostname == pat.lower().strip() or (pat.startswith(".") and hostname.endswith(pat.lower().strip()))
                for pat in allowlist
                if pat
            )
            if not allowed:
                raise McpConnectionError(f"MCP server URL not allowed by egress policy: {url}", server_url=url)
        self._response_size_limit = (
            response_size_limit if response_size_limit is not None else settings.mcp_response_size_limit_bytes
        )
        self._max_retries = max(0, max_retries if max_retries is not None else settings.mcp_max_retries)
        self._retry_base_delay_ms = (
            retry_base_delay_ms if retry_base_delay_ms is not None else settings.mcp_retry_base_delay_ms
        )
        self._request_id = 0
        self._session_id: str | None = None
        self._connected = False
        self._server_info: dict[str, Any] = {}

        timeouts = timeouts or {}
        connect_s = timeouts.get("connect_ms", settings.mcp_connect_timeout_ms) / 1000.0
        call_s = timeouts.get("call_ms", settings.mcp_call_timeout_ms) / 1000.0
        read_s = timeouts.get("read_ms", settings.mcp_read_timeout_ms) / 1000.0

        self._timeout = httpx.Timeout(connect=connect_s, read=read_s, write=call_s, pool=connect_s)
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self._headers,
            },
            follow_redirects=False,
        )

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _post_and_read(self, payload: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        """POST JSON to the MCP server, stream the response, and enforce size limits.

        Returns (status_code, headers_dict, body_text).
        Retries on connection errors and timeouts with exponential backoff.
        Captures and sends the Mcp-Session-Id header per the Streamable HTTP spec.
        """
        last_exc: McpError | None = None
        method = payload.get("method", "unknown")
        extra_headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        for attempt in range(1 + self._max_retries):
            try:
                async with self._client.stream(
                    "POST",
                    self._url,
                    json=payload,
                    headers=extra_headers,
                ) as response:
                    session_id = response.headers.get("mcp-session-id")
                    if session_id:
                        self._session_id = session_id
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self._response_size_limit:
                        raise McpResponseTooLarge(
                            f"Response Content-Length {declared} exceeds limit of {self._response_size_limit} bytes",
                            server_url=self._url,
                            limit_bytes=self._response_size_limit,
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._response_size_limit:
                            raise McpResponseTooLarge(
                                f"Response size exceeds limit of {self._response_size_limit} bytes",
                                server_url=self._url,
                                limit_bytes=self._response_size_limit,
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks).decode("utf-8", errors="replace")
                    headers_dict = dict(response.headers)
                    return response.status_code, headers_dict, body
            except McpResponseTooLarge:
                raise
            except httpx.ConnectError as exc:
                last_exc = McpConnectionError(f"Failed to connect to MCP server: {exc}", server_url=self._url)
                last_exc.__cause__ = exc
            except httpx.TimeoutException as exc:
                last_exc = McpTimeoutError(f"MCP request timed out: {exc}", server_url=self._url)
                last_exc.__cause__ = exc
            except httpx.HTTPError as exc:
                last_exc = McpConnectionError(f"MCP transport error: {exc}", server_url=self._url)
                last_exc.__cause__ = exc
            if attempt < self._max_retries:
                delay_s = (self._retry_base_delay_ms / 1000.0) * (2**attempt)
                logger.warning(
                    "mcp.retry [%s] %s attempt=%d/%d delay=%.1fs error=%s",
                    self._url,
                    method,
                    attempt + 1,
                    self._max_retries,
                    delay_s,
                    last_exc,
                )
                await asyncio.sleep(delay_s)
        raise last_exc  # type: ignore[misc]

    async def _send_jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request and return the result.

        Handles both direct JSON responses and SSE-streamed responses.
        Enforces the configured response size limit.
        """
        request_id = self._next_request_id()
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id,
        }
        if params is not None:
            payload["params"] = params

        logger.debug(
            "mcp.jsonrpc.send [%s] %s id=%s payload=%s", self._url, method, request_id, json.dumps(payload)[:500]
        )
        status_code, headers, body = await self._post_and_read(payload)
        logger.debug("mcp.jsonrpc.recv [%s] %s status=%d body=%s", self._url, method, status_code, body[:500])

        content_type = headers.get("content-type", "")

        if "text/event-stream" in content_type:
            return self._parse_sse_response(body, request_id)

        return self._parse_json_response_from_text(body, status_code, request_id)

    def _extract_jsonrpc_result(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate a parsed JSON-RPC envelope and return the result.

        Raises McpProtocolError on JSON-RPC error responses.
        """
        if "error" in data:
            error = data["error"]
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise McpProtocolError(f"JSON-RPC error: {message}", server_url=self._url, code=code)
        return data.get("result", {})

    def _parse_json_response_from_text(self, body: str, status_code: int, request_id: int) -> dict[str, Any]:
        """Parse a direct JSON-RPC response from body text."""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise McpProtocolError(f"Invalid JSON in MCP response: {exc}", server_url=self._url) from exc

        if not isinstance(data, dict):
            raise McpProtocolError("MCP response is not a JSON object", server_url=self._url)

        return self._extract_jsonrpc_result(data)

    def _parse_sse_response(self, body: str, request_id: int) -> dict[str, Any]:
        """Parse an SSE-streamed response, assembling the final JSON-RPC result.

        Reads event: message lines and extracts the last complete JSON-RPC
        response from the data fields.
        """
        event_type: str | None = None
        data_lines: list[str] = []
        last_result: dict[str, Any] | None = None

        for line in body.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
                continue

            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
                continue

            if line.strip() == "" and data_lines:
                if event_type == "message" or event_type is None:
                    last_result = self._try_parse_sse_event(data_lines, last_result)
                data_lines = []
                event_type = None

        if data_lines and (event_type == "message" or event_type is None):
            last_result = self._try_parse_sse_event(data_lines, last_result)

        if last_result is None:
            raise McpProtocolError("No valid JSON-RPC result found in SSE stream", server_url=self._url)

        return last_result

    def _try_parse_sse_event(self, data_lines: list[str], fallback: dict[str, Any] | None) -> dict[str, Any] | None:
        """Try to parse assembled SSE data lines as a JSON-RPC response."""
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return fallback
        if not isinstance(parsed, dict):
            return fallback
        try:
            return self._extract_jsonrpc_result(parsed)
        except McpProtocolError:
            return fallback

    async def connect(self) -> dict[str, Any]:
        """Perform the MCP initialize handshake.

        Sends an initialize request, verifies the server's protocol version
        is supported, then sends an initialized notification.

        Returns the server's capabilities and info from the initialize response.
        """
        result = await self._send_jsonrpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "shu", "version": "1.0"},
            },
        )

        server_version = result.get("protocolVersion", "")
        if server_version not in MCP_SUPPORTED_VERSIONS:
            raise McpProtocolError(
                f"Unsupported MCP protocol version: {server_version!r} "
                f"(supported: {', '.join(sorted(MCP_SUPPORTED_VERSIONS))})",
                server_url=self._url,
            )

        self._server_info = result.get("serverInfo", {})
        logger.info(
            "mcp.connected [%s] server=%s protocol=%s",
            self._url,
            self._server_info.get("name"),
            server_version,
        )

        await self._send_notification("notifications/initialized")
        self._connected = True
        return result

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected).

        Retries on transient connection/timeout failures.
        """
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._post_and_read(payload)

    async def _ensure_connected(self) -> None:
        """Perform the initialize handshake if not already connected."""
        if not self._connected:
            await self.connect()

    async def list_tools(self) -> list[McpToolInfo]:
        """Enumerate available tools from the MCP server."""
        await self._ensure_connected()
        result = await self._send_jsonrpc("tools/list")
        raw_tools = result.get("tools", [])
        tools = []
        for t in raw_tools:
            if not isinstance(t, dict) or "name" not in t:
                continue
            name = t["name"]
            if "__" in name:
                logger.warning("mcp.tool_skipped [%s] name=%s reason=contains '__' delimiter", self._url, name)
                continue
            tools.append(
                McpToolInfo(
                    name=name,
                    description=t.get("description"),
                    input_schema=t.get("inputSchema"),
                )
            )
        logger.info("mcp.tools_discovered [%s] count=%d", self._url, len(tools))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> McpToolResult:
        """Invoke a tool on the MCP server."""
        await self._ensure_connected()
        params: dict[str, Any] = {"name": name, "arguments": arguments or {}}

        logger.info("mcp.tool_call [%s] tool=%s", self._url, name)
        result = await self._send_jsonrpc("tools/call", params)

        content = result.get("content", [])
        is_error = result.get("isError", False)
        return McpToolResult(content=content, is_error=is_error)

    async def health_check(self) -> bool:
        """Lightweight connectivity test via the initialize handshake."""
        try:
            await self.connect()
            return True
        except McpError:
            return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
