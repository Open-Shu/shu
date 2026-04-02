"""MCP plugin adapter — bridges an MCP server connection to the Shu Plugin protocol."""

from __future__ import annotations

import json
import time
from typing import Any

from shu.core.logging import get_logger
from shu.models.mcp_server_connection import McpServerConnection
from shu.plugins.base import PluginResult
from shu.plugins.base_adapter import BasePluginAdapter
from shu.plugins.mcp_client import (
    McpClient,
    McpConnectionError,
    McpError,
    McpProtocolError,
    McpResponseTooLarge,
    McpTimeoutError,
    McpToolResult,
)

logger = get_logger(__name__)


class McpPluginAdapter(BasePluginAdapter):
    """Adapts an MCP server connection to the Shu Plugin protocol.

    One adapter instance per connection. Each MCP tool is exposed as an op.
    Implements the Plugin protocol for use by PluginExecutor and the feed scheduler.
    """

    def __init__(self, connection: McpServerConnection, client: McpClient | None = None) -> None:
        super().__init__(
            name=f"mcp:{connection.name}",
            version=(connection.server_info or {}).get("version", "1.0"),
            tool_configs=connection.tool_configs,
            discovered_tools=connection.discovered_tools,
        )
        self._connection = connection
        self._client = client

    # TODO: We should get rid of all instances that call these plugin functions. They are too generic to be applied correctly.
    def get_schema(self) -> dict[str, Any] | None:
        """Build a combined JSON Schema with op enum from enabled tools.

        Tool-specific properties are flattened into the top-level properties
        with x-ui.show_when rules so SchemaForm can conditionally show them
        based on the selected op.
        """
        enabled = self._enabled_tools()
        if not enabled:
            return None

        discovered = self._discovered_tools_by_name()
        op_names = sorted(enabled.keys())
        op_labels = {}
        op_help = {}
        merged_properties: dict[str, Any] = {}
        prop_ops: dict[str, list[str]] = {}

        for tool_name in op_names:
            tool_info = discovered.get(tool_name, {})
            cfg = enabled[tool_name]
            flags = []
            if cfg.get("chat_callable", True):
                flags.append("chat")
            if cfg.get("feed_eligible", False):
                flags.append("feed")
            description = tool_info.get("description", tool_name)
            op_labels[tool_name] = f"{description} ({'+'.join(flags) or 'none'})"
            op_help[tool_name] = description

            input_schema = tool_info.get("inputSchema")
            if input_schema and isinstance(input_schema, dict):
                for prop_name, prop_def in input_schema.get("properties", {}).items():
                    if prop_name not in merged_properties:
                        merged_properties[prop_name] = dict(prop_def)
                        prop_ops[prop_name] = [tool_name]
                    elif merged_properties[prop_name].get("enum") == prop_def.get("enum"):
                        prop_ops[prop_name].append(tool_name)

        for prop_name, ops in prop_ops.items():
            if set(ops) != set(op_names):
                merged_properties[prop_name]["x-ui"] = {
                    **(merged_properties[prop_name].get("x-ui") or {}),
                    "show_when": {"field": "op", "in": ops},
                }

        properties: dict[str, Any] = {
            "op": {
                "type": "string",
                "enum": op_names,
                "description": "MCP tool to invoke",
                "x-ui": {
                    "help": f"Select a tool from {self._connection.name}",  # noqa: S608  # nosec B608
                    "enum_labels": op_labels,
                    "enum_help": op_help,
                },
            },
            **merged_properties,
        }

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": properties,
            "required": ["op"],
            "additionalProperties": True,
        }

    async def _call_tool(self, op: str, params: dict[str, Any]) -> McpToolResult | PluginResult:
        """Call an MCP tool, mapping errors to PluginResult.

        Returns McpToolResult on success, or PluginResult on error.
        """
        _internal_keys = {"op", "kb_id", "reset_cursor", "debug", "__schedule_id"}
        arguments = {k: v for k, v in params.items() if k not in _internal_keys and not k.startswith("__")}
        start = time.monotonic()

        try:
            result = await self._client.call_tool(op, arguments or None)
        except McpConnectionError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_connection_error", error=str(exc))
            return PluginResult.err(str(exc), code="mcp_connection_error")
        except McpTimeoutError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_timeout", error=str(exc))
            return PluginResult.err(str(exc), code="mcp_timeout")
        except McpResponseTooLarge as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_response_too_large", error=str(exc))
            return PluginResult.err(str(exc), code="mcp_response_too_large")
        except McpProtocolError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_protocol_error", error=str(exc))
            return PluginResult.err(str(exc), code="mcp_protocol_error")
        except McpError as exc:
            self._log_tool_call(op, start, "error", 0, code="mcp_server_error", error=str(exc))
            return PluginResult.err(str(exc), code="mcp_server_error")

        result_size = len(json.dumps(result.content)) if result.content else 0

        if result.is_error:
            error_text = self._extract_text_content(result)
            self._log_tool_call(op, start, "error", result_size, code="mcp_server_error", error=error_text)
            return PluginResult.err(error_text or "MCP tool returned an error", code="mcp_server_error")

        self._log_tool_call(op, start, "ok", result_size)
        return result

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

    def _extract_text_content(self, result: McpToolResult) -> str:
        """Extract text from MCP tool result content blocks."""
        texts = []
        for block in result.content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)
