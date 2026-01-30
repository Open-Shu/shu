from __future__ import annotations

from typing import Any


# Local ToolResult shim for tests to avoid importing shu.*
class ToolResult:
    def __init__(self, status: str, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None):
        self.status = status
        self.data = data or {}
        self.error = error

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None):
        return cls(status="success", data=data)


class TestSchemaPlugin:
    name = "test_schema"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["run"],
                    "default": "run",
                    "x-ui": {
                        "help": "Run schema echo test.",
                        "enum_labels": {"run": "Run"},
                        "enum_help": {"run": "Return the provided query value"},
                    },
                },
                "q": {"type": "string"},
            },
            "required": ["q"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "echo": {"type": "string"},
            },
            "required": ["echo"],
            "additionalProperties": True,
        }

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> ToolResult:
        return ToolResult.ok({"echo": params["q"]})
