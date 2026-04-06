"""API plugin adapter — bridges an API server connection to the Shu Plugin protocol via HTTP."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from shu.core.logging import get_logger
from shu.models.api_server_connection import ApiServerConnection
from shu.plugins.base import PluginResult
from shu.plugins.base_adapter import BasePluginAdapter

logger = get_logger(__name__)

_INTERNAL_KEYS = {"op", "kb_id", "reset_cursor", "debug", "__schedule_id"}


@dataclass
class ApiToolResult:
    """Wrapper for a successful HTTP response, matching the content interface expected by BasePluginAdapter."""

    content: Any = None


class ApiPluginAdapter(BasePluginAdapter):
    """Adapts an API server connection to the Shu Plugin protocol.

    One adapter instance per connection. Each discovered HTTP operation is
    exposed as an op. Implements the Plugin protocol for use by PluginExecutor
    and the feed scheduler.
    """

    def __init__(
        self,
        connection: ApiServerConnection,
        http_client: httpx.AsyncClient | None = None,
        credential: str | None = None,
    ) -> None:
        super().__init__(
            name=f"api:{connection.name}",
            version="1.0",
            tool_configs=connection.tool_configs,
            discovered_tools=connection.discovered_tools,
        )
        self._connection = connection
        self._http_client = http_client
        self._credential = credential
        self._operations: dict[str, dict] = self._index_operations()
        self._response_size_limit = connection.response_size_limit_bytes or 10 * 1024 * 1024
        timeouts = connection.timeouts or {}
        self._timeout = httpx.Timeout(
            connect=timeouts.get("connect_ms", 5000) / 1000,
            read=timeouts.get("read_ms", 30000) / 1000,
            write=30.0,
            pool=30.0,
        )

    def _index_operations(self) -> dict[str, dict]:
        """Index discovered_tools by name, filtering to those with HTTP operation metadata."""
        tools = self._discovered_tools or []
        return {
            t["name"]: t
            for t in tools
            if isinstance(t, dict) and "name" in t and "method" in t
        }

    async def _call_tool(self, op: str, params: dict[str, Any]) -> ApiToolResult | PluginResult:
        """Execute an HTTP request for the given operation.

        Returns ApiToolResult on success, or PluginResult on error.
        """
        start = time.monotonic()

        operation = self._operations.get(op)
        if not operation:
            self._log_tool_call(op, start, "error", 0, code="unknown_operation", error=f"No operation: {op}")
            return PluginResult.err(f"Unknown operation: {op}", code="unknown_operation")

        validation_error = self._validate_params(op, params)
        if validation_error:
            self._log_tool_call(op, start, "error", 0, code="validation_error", error=validation_error)
            return PluginResult.err(validation_error, code="validation_error")

        clean_params = self._strip_internal_keys(params)
        url = self._build_url(operation, clean_params)
        query = self._build_query_params(operation, clean_params)
        body = self._build_request_body(operation, clean_params)
        headers = self._build_headers()
        method = operation["method"].upper()

        content_type = operation.get("content_type")
        request_kwargs = self._build_body_kwargs(body, content_type)

        try:
            response_data = await self._execute_request(method, url, query, headers, request_kwargs)
        except httpx.TimeoutException as exc:
            self._log_tool_call(op, start, "error", 0, code="timeout", error=str(exc))
            return PluginResult.err(f"Request timed out: {exc}", code="timeout")
        except httpx.ConnectError as exc:
            self._log_tool_call(op, start, "error", 0, code="connection_error", error=str(exc))
            return PluginResult.err(f"Connection failed: {exc}", code="connection_error")

        if isinstance(response_data, PluginResult):
            self._log_tool_call(
                op, start, "error", 0,
                code=response_data.error.get("code", "http_error") if response_data.error else "http_error",
                error=response_data.error.get("message", "") if response_data.error else "",
            )
            return response_data

        result_size = len(json.dumps(response_data)) if response_data else 0
        self._log_tool_call(op, start, "ok", result_size)
        return ApiToolResult(content=response_data)

    async def _execute_request(
        self,
        method: str,
        url: str,
        query: dict[str, Any],
        headers: dict[str, str],
        body_kwargs: dict[str, Any],
    ) -> dict[str, Any] | PluginResult:
        """Send the HTTP request, stream response with size enforcement, and handle status codes."""
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout)
        own_client = self._http_client is None
        try:
            async with client.stream(
                method,
                url,
                params=query or None,
                headers=headers or None,
                timeout=self._timeout,
                **body_kwargs,
            ) as response:
                if response.status_code == 429:
                    return await self._retry_with_backoff(method, url, query, body_kwargs, headers)

                if response.status_code >= 400:
                    error_body = await self._read_response_body(response)
                    return PluginResult.err(
                        f"HTTP {response.status_code}: {error_body[:500]}",
                        code=f"http_{response.status_code}",
                    )

                body_text = await self._read_response_body(response)
        finally:
            if own_client:
                await client.aclose()

        return self._parse_response(body_text)

    async def _read_response_body(self, response: httpx.Response) -> str:
        """Read the streamed response body, enforcing the size limit."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._response_size_limit:
                raise httpx.DecodingError(
                    f"Response exceeded size limit ({self._response_size_limit} bytes)"
                )
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _parse_response(self, body_text: str) -> dict[str, Any]:
        """Parse the response body as JSON, falling back to a text wrapper."""
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"text": body_text}

    def _assemble_response_data(self, raw_result: ApiToolResult) -> dict[str, Any]:
        """Convert an ApiToolResult into a plain dict for ingest processing."""
        content = raw_result.content
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            return {"text": content}
        return {"data": content}

    def _build_url(self, operation: dict, params: dict[str, Any]) -> str:
        """Substitute path parameters into the URL template and prepend base_url."""
        base_url = (self._connection.base_url or self._connection.url or "").rstrip("/")
        path = operation.get("path", "")
        path_params = operation.get("path_params", [])

        for name in path_params:
            value = params.get(f"path_{name}") or params.get(name)
            if value is not None:
                path = path.replace(f"{{{name}}}", str(value))

        return f"{base_url}{path}"

    def _build_query_params(self, operation: dict, params: dict[str, Any]) -> dict[str, Any]:
        """Extract query parameters from the params dict based on operation metadata."""
        query_param_names = operation.get("query_params", [])
        result: dict[str, Any] = {}
        for name in query_param_names:
            value = params.get(f"query_{name}") or params.get(name)
            if value is not None:
                result[name] = value

        # Inject query-type auth credential
        auth_config = self._connection.auth_config
        if auth_config and auth_config.get("type") == "query" and self._credential:
            result[auth_config.get("name", "api_key")] = self._credential

        return result

    def _build_request_body(self, operation: dict, params: dict[str, Any]) -> dict[str, Any] | None:
        """Extract body parameters — everything not path, query, or internal."""
        if not operation.get("has_body", False):
            return None

        path_params = set(operation.get("path_params", []))
        query_params = set(operation.get("query_params", []))
        reserved = path_params | query_params
        prefixed_reserved = {f"path_{p}" for p in path_params} | {f"query_{q}" for q in query_params}
        exclude = reserved | prefixed_reserved

        body = {k: v for k, v in params.items() if k not in exclude}
        return body or None

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, injecting auth from auth_config metadata + stored credential."""
        headers: dict[str, str] = {}
        auth_config = self._connection.auth_config
        if not auth_config or not self._credential:
            return headers

        auth_type = auth_config.get("type", "header")
        header_name = auth_config.get("name", "Authorization")
        prefix = auth_config.get("prefix", "")

        if auth_type == "header":
            value = f"{prefix}{self._credential}" if prefix else self._credential
            headers[header_name] = value
        elif auth_type == "query":
            pass  # Query auth is handled in _build_query_params

        return headers

    def _validate_params(self, op: str, params: dict[str, Any]) -> str | None:
        """Check required parameters from the operation's inputSchema.

        Returns an error message string if validation fails, or None if valid.
        """
        operation = self._operations.get(op, {})
        input_schema = operation.get("inputSchema", {})
        required = input_schema.get("required", [])

        missing = [r for r in required if r not in params or params[r] is None]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None

    async def _retry_with_backoff(
        self,
        method: str,
        url: str,
        query: dict[str, Any],
        body_kwargs: dict[str, Any],
        headers: dict[str, str],
        max_retries: int = 3,
    ) -> dict[str, Any] | PluginResult:
        """Retry a request with exponential backoff for 429 responses.

        Respects Retry-After header and caps total wait at the configured read timeout.
        """
        budget_start = time.monotonic()
        budget_seconds = self._timeout.read or 30.0

        client = self._http_client or httpx.AsyncClient(timeout=self._timeout)
        own_client = self._http_client is None

        try:
            for attempt in range(max_retries):
                delay = min(2**attempt, budget_seconds - (time.monotonic() - budget_start))
                if delay <= 0:
                    return PluginResult.err("Retry budget exhausted", code="retry_budget_exhausted")

                await asyncio.sleep(delay)

                elapsed = time.monotonic() - budget_start
                if elapsed >= budget_seconds:
                    return PluginResult.err("Retry budget exhausted", code="retry_budget_exhausted")

                try:
                    async with client.stream(
                        method,
                        url,
                        params=query or None,
                        headers=headers or None,
                        timeout=self._timeout,
                        **body_kwargs,
                    ) as response:
                        if response.status_code == 429:
                            retry_after = response.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    retry_delay = float(retry_after)
                                    remaining = budget_seconds - (time.monotonic() - budget_start)
                                    if retry_delay <= remaining:
                                        await asyncio.sleep(retry_delay)
                                    else:
                                        return PluginResult.err(
                                            "Retry-After exceeds budget", code="retry_budget_exhausted"
                                        )
                                except ValueError:
                                    pass
                            continue

                        if response.status_code >= 400:
                            error_body = await self._read_response_body(response)
                            return PluginResult.err(
                                f"HTTP {response.status_code}: {error_body[:500]}",
                                code=f"http_{response.status_code}",
                            )

                        body_text = await self._read_response_body(response)
                        return self._parse_response(body_text)

                except httpx.TimeoutException:
                    continue
                except httpx.ConnectError as exc:
                    return PluginResult.err(f"Connection failed during retry: {exc}", code="connection_error")

            return PluginResult.err("Max retries exceeded for 429 response", code="rate_limited")
        finally:
            if own_client:
                await client.aclose()

    def _strip_internal_keys(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove internal keys from params before building the HTTP request."""
        return {
            k: v for k, v in params.items()
            if k not in _INTERNAL_KEYS and not k.startswith("__")
        }

    def _build_body_kwargs(self, body: dict[str, Any] | None, content_type: str | None) -> dict[str, Any]:
        """Select the appropriate httpx body encoding based on content_type."""
        if body is None:
            return {}

        if content_type == "application/x-www-form-urlencoded":
            return {"data": body}
        if content_type and content_type.startswith("multipart/"):
            return {"files": body}
        return {"json": body}
