from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional

# Local ToolResult shim
class ToolResult:
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data or {}
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None):
        return cls(status="success", data=data)

    @classmethod
    def err(cls, message: str):
        return cls(status="error", error={"message": message})


class TestSlowPlugin:
    name = "test_slow"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Run a slow operation for testing.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Sleep for the provided number of seconds"}}},
                "sleep_seconds": {"type": "number", "minimum": 0, "maximum": 10},
            },
            "required": ["sleep_seconds"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> ToolResult:
        s = float(params.get("sleep_seconds", 0))
        await asyncio.sleep(max(0.0, min(10.0, s)))
        return ToolResult.ok({"slept": s})

