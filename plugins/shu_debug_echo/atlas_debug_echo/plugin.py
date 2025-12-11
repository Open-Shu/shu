from __future__ import annotations
from typing import Any, Dict, Optional
import os

# Local ToolResult shim to avoid importing shu.* from plugins
class ToolResult:
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data or {}
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None):
        return cls(status="success", data=data)


def _force_invalid_allowed() -> bool:
    # Gate by explicit debug flag or non-production environment via env only
    debug = os.environ.get("SHU_DEBUG", "false").lower() == "true"
    env = os.environ.get("SHU_ENVIRONMENT", "development").lower()
    return debug or env != "production"


class EchoPlugin:
    name = "test_echo"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        # Base schema
        schema: Dict[str, Any] = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Execute the echo test.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Echo the provided message N times"}}},
                "message": {"type": "string"},
                "count": {"type": "integer", "minimum": 0, "default": 1},
            },
            "required": ["message"],
            "additionalProperties": True,
        }
        # Expose test hook only in debug/non-production
        if _force_invalid_allowed():
            schema["properties"]["force_invalid_output"] = {"type": "boolean", "default": False}
        return schema

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "echo": {"type": "object"},
                "user_id": {"type": "string"},
                "agent_key": {"type": ["string", "null"]},
            },
            "required": ["echo", "user_id"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> ToolResult:
        # Only honor the test hook in debug/non-production
        if _force_invalid_allowed() and params.get("force_invalid_output"):
            # Omit required 'echo' to deliberately violate output schema for testing
            return ToolResult.ok({
                "user_id": getattr(context, "user_id", None),
                "agent_key": getattr(context, "agent_key", None),
            })
        return ToolResult.ok({
            "echo": params,
            "user_id": getattr(context, "user_id", None),
            "agent_key": getattr(context, "agent_key", None),
        })

