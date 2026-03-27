"""MCP plugin adapter — bridges an MCP server connection to the Shu Plugin protocol."""

from __future__ import annotations

import json
import time
from typing import Any

from shu.core.logging import get_logger
from shu.models.mcp_server_connection import McpServerConnection
from shu.plugins.base import ExecuteContext, Plugin, PluginResult
from shu.plugins.mcp_client import (
    McpClient,
    McpConnectionError,
    McpError,
    McpProtocolError,
    McpResponseTooLarge,
    McpTimeoutError,
    McpToolResult,
)
from shu.utils.path_access import DotPath

logger = get_logger(__name__)


class McpPluginAdapter:
    """Adapts an MCP server connection to the Shu Plugin protocol.

    One adapter instance per connection. Each MCP tool is exposed as an op.
    Implements the Plugin protocol for use by PluginExecutor and the feed scheduler.
    """

    def __init__(self, connection: McpServerConnection, client: McpClient) -> None:
        self._connection = connection
        self._client = client
        self.name: str = f"mcp:{connection.name}"
        server_info = connection.server_info or {}
        self.version: str = server_info.get("version", "1.0")

    def _enabled_tools(self) -> dict[str, dict[str, Any]]:
        """Return tool_configs entries that are enabled."""
        configs = self._connection.tool_configs or {}
        return {name: cfg for name, cfg in configs.items() if cfg.get("enabled", True)}

    def _discovered_tools_by_name(self) -> dict[str, dict[str, Any]]:
        """Index discovered tools by name for schema lookup."""
        tools = self._connection.discovered_tools or []
        return {t["name"]: t for t in tools if isinstance(t, dict) and "name" in t}

    def get_schema(self) -> dict[str, Any] | None:
        """Build a combined JSON Schema with op enum from enabled tools.

        Each tool's inputSchema properties are merged as conditional
        (using allOf/if/then) so the UI can show per-op fields.
        """
        enabled = self._enabled_tools()
        if not enabled:
            return None

        discovered = self._discovered_tools_by_name()
        op_names = sorted(enabled.keys())
        op_labels = {}
        op_help = {}
        all_of: list[dict[str, Any]] = []

        for tool_name in op_names:
            tool_info = discovered.get(tool_name, {})
            cfg = enabled[tool_name]
            tool_type = cfg.get("type", "chat_callable")
            description = tool_info.get("description", tool_name)
            op_labels[tool_name] = f"{description} ({tool_type})"
            op_help[tool_name] = description

            input_schema = tool_info.get("inputSchema")
            if input_schema and isinstance(input_schema, dict):
                tool_properties = input_schema.get("properties", {})
                tool_required = input_schema.get("required", [])
                if tool_properties:
                    all_of.append({
                        "if": {"properties": {"op": {"const": tool_name}}},
                        "then": {
                            "properties": tool_properties,
                            "required": tool_required,
                        },
                    })

        schema: dict[str, Any] = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": op_names,
                    "description": "MCP tool to invoke",
                    "x-ui": {
                        "help": f"Select a tool from {self._connection.name}",  # nosec B608
                        "enum_labels": op_labels,
                        "enum_help": op_help,
                    },
                },
            },
            "required": ["op"],
            "additionalProperties": True,
        }

        if all_of:
            schema["allOf"] = all_of

        return schema

    def get_output_schema(self) -> dict[str, Any] | None:
        """MCP output schemas are ephemeral — return None."""
        return None

    async def execute(self, params: dict[str, Any], context: ExecuteContext, host: Any) -> PluginResult:
        """Dispatch to the appropriate MCP tool based on params['op']."""
        op = params.get("op")
        if not op:
            return PluginResult.err("Missing required parameter: op", code="missing_op")

        enabled = self._enabled_tools()
        if op not in enabled:
            return PluginResult.err(f"Unknown or disabled tool: {op}", code="unknown_op")

        tool_config = enabled[op]
        tool_type = tool_config.get("type", "chat_callable")

        if tool_type == "chat_callable":
            return await self._execute_chat_callable(op, params)

        if tool_type == "ingest":
            return await self._execute_ingest(op, params, tool_config, host)

        return PluginResult.err(f"Unknown tool type: {tool_type}", code="unknown_tool_type")

    async def _call_tool(self, op: str, params: dict[str, Any]) -> McpToolResult | PluginResult:
        """Call an MCP tool, mapping errors to PluginResult.

        Returns McpToolResult on success, or PluginResult on error.
        """
        arguments = {k: v for k, v in params.items() if k != "op"}
        start = time.monotonic()

        try:
            result = await self._client.call_tool(op, arguments or None)
        except McpConnectionError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_connection_error")
            return PluginResult.err(str(exc), code="mcp_connection_error")
        except McpTimeoutError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_timeout")
            return PluginResult.err(str(exc), code="mcp_timeout")
        except McpResponseTooLarge as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_response_too_large")
            return PluginResult.err(str(exc), code="mcp_response_too_large")
        except McpProtocolError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_protocol_error")
            return PluginResult.err(str(exc), code="mcp_protocol_error")
        except McpError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_server_error")
            return PluginResult.err(str(exc), code="mcp_server_error")

        result_size = len(json.dumps(result.content)) if result.content else 0

        if result.is_error:
            error_text = self._extract_text_content(result)
            self._log_tool_call(op, start, "error", result_size, code="mcp_server_error")
            return PluginResult.err(error_text or "MCP tool returned an error", code="mcp_server_error")

        self._log_tool_call(op, start, "ok", result_size)
        return result

    async def _execute_chat_callable(self, op: str, params: dict[str, Any]) -> PluginResult:
        """Execute a chat-callable MCP tool and return the result."""
        outcome = await self._call_tool(op, params)
        if isinstance(outcome, PluginResult):
            return outcome
        return PluginResult.ok({"result": outcome.content})

    async def _execute_ingest(
        self, op: str, params: dict[str, Any], tool_config: dict[str, Any], host: Any
    ) -> PluginResult:
        """Execute an ingest-annotated MCP tool.

        Calls the MCP tool, extracts items via collection_field, maps fields
        per the ingest config, and routes each through host.kb.ingest_text()
        or host.kb.ingest_document().

        Supports pagination: if cursor_field and cursor_param are configured,
        loops until no cursor is returned. Persists the last cursor via
        host.cursor between feed runs for incremental sync.
        """
        ingest_cfg = tool_config.get("ingest", {})
        if not ingest_cfg:
            return PluginResult.err("Missing ingest configuration for tool", code="missing_ingest_config")

        kb_ids = getattr(host.kb, "_knowledge_base_ids", []) if host and hasattr(host, "kb") else []
        if not kb_ids:
            return PluginResult.err("No knowledge base bound for ingest", code="no_knowledge_base")

        kb_id = kb_ids[0]
        field_mapping = ingest_cfg.get("field_mapping", {})
        collection_field = ingest_cfg.get("collection_field")
        method = ingest_cfg.get("method", "text")
        static_attributes = ingest_cfg.get("attributes", {})
        cursor_field = ingest_cfg.get("cursor_field")
        cursor_param = ingest_cfg.get("cursor_param")

        call_params = dict(params)
        if cursor_field and cursor_param:
            saved_cursor = await self._load_cursor(host, kb_id)
            if saved_cursor:
                call_params[cursor_param] = saved_cursor

        ingested_count = 0
        skipped_count = 0
        error_count = 0
        total_items = 0
        warnings: list[str] = []
        last_cursor: str | None = None

        while True:
            outcome = await self._call_tool(op, call_params)
            if isinstance(outcome, PluginResult):
                return outcome

            response_data = self._assemble_response_data(outcome)
            items = self._extract_items(response_data, collection_field)
            total_items += len(items)

            for idx, item in enumerate(items):
                counts = await self._ingest_item(
                    item, field_mapping, method, static_attributes, kb_id, host,
                    idx=(ingested_count + skipped_count + error_count),
                    op=op, warnings=warnings,
                )
                ingested_count += counts[0]
                skipped_count += counts[1]
                error_count += counts[2]

            if not cursor_field or not cursor_param:
                break

            next_cursor = DotPath.get(response_data, cursor_field)
            if not next_cursor or next_cursor == last_cursor:
                break

            last_cursor = str(next_cursor)
            call_params[cursor_param] = last_cursor

        if cursor_field and cursor_param and last_cursor:
            await self._save_cursor(host, kb_id, last_cursor)

        logger.info(
            "mcp.ingest_complete [%s/%s] ingested=%d skipped=%d errors=%d total=%d",
            self.name, op, ingested_count, skipped_count, error_count, total_items,
        )

        return PluginResult.ok(
            data={
                "ingested_count": ingested_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "total_items": total_items,
            },
            warnings=warnings or None,
        )

    async def _ingest_item(
        self,
        item: dict[str, Any],
        field_mapping: dict[str, str],
        method: str,
        static_attributes: dict[str, str],
        kb_id: str,
        host: Any,
        idx: int,
        op: str,
        warnings: list[str],
    ) -> tuple[int, int, int]:
        """Ingest a single item. Returns (ingested, skipped, errored) counts."""
        mapped = self._map_fields(item, field_mapping, idx, warnings)
        if mapped is None:
            return (0, 1, 0)

        mapped_attrs = {**(static_attributes or {}), **(mapped.get("attributes") or {})}

        try:
            if method == "document":
                await host.kb.ingest_document(
                    kb_id,
                    file_bytes=mapped["content"].encode("utf-8") if isinstance(mapped["content"], str) else mapped["content"],
                    filename=mapped.get("filename", mapped["title"]),
                    mime_type=mapped.get("mime_type", "text/plain"),
                    source_id=mapped["source_id"],
                    source_url=mapped.get("source_url"),
                    attributes=mapped_attrs or None,
                )
            else:
                await host.kb.ingest_text(
                    kb_id,
                    title=mapped["title"],
                    content=mapped["content"],
                    source_id=mapped["source_id"],
                    source_url=mapped.get("source_url"),
                    attributes=mapped_attrs or None,
                )
            return (1, 0, 0)
        except Exception as exc:
            warnings.append(f"Item {idx}: ingest failed: {exc}")
            logger.warning("mcp.ingest_item_failed [%s/%s] item=%d: %s", self.name, op, idx, exc)
            return (0, 0, 1)

    async def _load_cursor(self, host: Any, kb_id: str) -> str | None:
        """Load the saved cursor from host.cursor if available."""
        if not hasattr(host, "cursor"):
            return None
        try:
            return await host.cursor.get(kb_id)
        except Exception:
            return None

    async def _save_cursor(self, host: Any, kb_id: str, cursor: str) -> None:
        """Persist the cursor via host.cursor for the next feed run."""
        if not hasattr(host, "cursor"):
            return
        try:
            await host.cursor.set(kb_id, cursor)
        except Exception:
            logger.warning("mcp.cursor_save_failed [%s] kb=%s", self.name, kb_id)

    def _assemble_response_data(self, result: McpToolResult) -> dict[str, Any]:
        """Assemble MCP tool result content into a single data dict.

        If the result contains a single text block with JSON, parse it.
        Otherwise return the raw content list.
        """
        text_blocks = [b for b in result.content if isinstance(b, dict) and b.get("type") == "text"]
        if len(text_blocks) == 1:
            try:
                parsed = json.loads(text_blocks[0].get("text", ""))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {"content": result.content}

    def _extract_items(self, data: dict[str, Any], collection_field: str | None) -> list[dict[str, Any]]:
        """Extract items from response data using the collection_field path."""
        if not collection_field:
            return [data]

        value = DotPath.get(data, collection_field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return [data]

    def _map_fields(
        self, item: dict[str, Any], field_mapping: dict[str, str], idx: int, warnings: list[str]
    ) -> dict[str, Any] | None:
        """Apply field mapping to extract title, content, source_id, source_url from an item.

        Returns None if required fields (title, content, source_id) are missing.
        """
        mapped: dict[str, Any] = {}
        for target_field in ("title", "content", "source_id"):
            source_path = field_mapping.get(target_field)
            if not source_path:
                warnings.append(f"Item {idx}: field mapping missing for required field '{target_field}'")
                return None
            value = DotPath.get(item, source_path)
            if value is None:
                warnings.append(f"Item {idx}: field '{target_field}' not found at path '{source_path}'")
                return None
            mapped[target_field] = str(value)

        for optional_field in ("source_url", "filename", "mime_type"):
            source_path = field_mapping.get(optional_field)
            if source_path:
                value = DotPath.get(item, source_path)
                if value is not None:
                    mapped[optional_field] = str(value)

        return mapped

    def _extract_text_content(self, result: McpToolResult) -> str:
        """Extract text from MCP tool result content blocks."""
        texts = []
        for block in result.content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)

    def _log_tool_call(
        self, op: str, start: float, status: str, result_size: int, code: str | None = None
    ) -> None:
        """Log a structured tool call event."""
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("mcp.tool_call [%s/%s] %dms status=%s size=%d code=%s", self.name, op, duration_ms, status, result_size, code)
