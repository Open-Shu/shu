from __future__ import annotations
from typing import Any, Dict, Optional

# Local ToolResult shim to avoid importing shu.* from plugins
class ToolResult:
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data or {}
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None):
        return cls(status="success", data=data)


class OutputBloatPlugin:
    name = "test_output_bloat"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        # Allow caller to request an approximate output size in bytes
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Generate a large output for UI stress testing.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Emit a string of the requested size"}}},
                "size": {"type": "integer", "minimum": 0, "default": 0},
                "char": {"type": "string", "default": "A"},
            },
            "required": ["size"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        # Unconstrained; intentionally allows large payloads for guardrail tests
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "blob": {"type": "string"},
                "size": {"type": "integer"},
            },
            "required": ["blob", "size"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> ToolResult:
        target_size = int(params.get("size") or 0)
        ch = str(params.get("char") or "A")
        if not ch:
            ch = "A"
        # Make a blob string approximately target_size bytes (UTF-8)
        blob = (ch * max(0, target_size))[:target_size]
        return ToolResult.ok({
            "blob": blob,
            "size": len(blob.encode("utf-8")),
        })

