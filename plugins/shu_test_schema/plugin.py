from __future__ import annotations
from typing import Any, Dict, Optional

# Local ToolResult shim for tests to avoid importing shu.*
class ToolResult:
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data or {}
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None):
        return cls(status="success", data=data)



class TestSchemaPlugin:
    name = "test_schema"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Run schema echo test.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Return the provided query value"}}},
                "q": {"type": "string"},
            },
            "required": ["q"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "echo": {"type": "string"},
            },
            "required": ["echo"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> ToolResult:
        return ToolResult.ok({"echo": params["q"]})

