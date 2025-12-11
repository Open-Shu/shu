"""
Plugins v1 base interfaces and models.

Intentionally minimal: plugin interface, execution context, and PluginResult.
Aligns with docs/contracts/PLUGIN_CONTRACT.md at a practical subset.
"""
from __future__ import annotations
from typing import Any, Dict, Optional, Protocol, runtime_checkable
from pydantic import BaseModel


class ExecuteContext(BaseModel):
    user_id: str
    agent_key: Optional[str] = None
    # Additional host-provided context fields can be added later (e.g., idempotency_key)


class PluginResult(BaseModel):
    status: str  # "success" | "error" | "timeout"
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    warnings: Optional[list[str]] = None
    citations: Optional[list[Dict[str, Any]]] = None

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None, warnings: Optional[list[str]] = None):
        return cls(status="success", data=data or {}, warnings=warnings)

    @classmethod
    def err(cls, message: str, code: str = "", details: Optional[Dict[str, Any]] = None):
        return cls(status="error", error={"code": code or "plugin_error", "message": message, "details": details or {}})


@runtime_checkable
class Plugin(Protocol):
    name: str
    version: str

    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Return a JSON schema for parameters if available."""
        ...

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        """Return a JSON schema for PluginResult.data when status == 'success' (optional)."""
        ...

    async def execute(self, params: Dict[str, Any], context: ExecuteContext, host: Any) -> PluginResult:
        ...

