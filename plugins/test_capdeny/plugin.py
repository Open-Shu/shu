from __future__ import annotations
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


class TestCapDenyPlugin:
    name = "test_capdeny"
    version = "1"
    _capabilities = ["identity"]  # align with manifest

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["try_secrets"], "default": "try_secrets", "x-ui": {"help": "Attempt to use a denied capability.", "enum_labels": {"try_secrets": "Try Secrets"}, "enum_help": {"try_secrets": "Call host.secrets without declaring capability; should be denied by host"}}},
            },
            "required": ["op"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> ToolResult:
        # Attempt to use an undeclared capability (should be denied at host boundary)
        try:
            if params.get("op") == "try_secrets":
                # This attribute should raise CapabilityDenied via Host.__getattr__
                _ = await host.secrets.get("anything")  # type: ignore[attr-defined]
        except Exception as e:
            return ToolResult.err(str(e))
        return ToolResult.ok({"ok": True})

