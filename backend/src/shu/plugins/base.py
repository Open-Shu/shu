"""Plugins v1 base interfaces and models.

Intentionally minimal: plugin interface, execution context, and PluginResult.
Aligns with docs/contracts/PLUGIN_CONTRACT.md at a practical subset.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class ExecuteContext(BaseModel):
    user_id: str
    agent_key: str | None = None
    # Additional host-provided context fields can be added later (e.g., idempotency_key)


class PluginResult(BaseModel):
    status: str  # "success" | "error" | "timeout"
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    warnings: list[str] | None = None
    citations: list[dict[str, Any]] | None = None

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None, warnings: list[str] | None = None) -> PluginResult:
        return cls(status="success", data=data or {}, warnings=warnings)

    @classmethod
    def err(cls, message: str, code: str = "", details: dict[str, Any] | None = None) -> PluginResult:
        return cls(
            status="error",
            error={"code": code or "plugin_error", "message": message, "details": details or {}},
        )


@runtime_checkable
class Plugin(Protocol):
    name: str
    version: str

    def get_schema(self) -> dict[str, Any] | None:
        """Return a combined JSON schema for all operations.

        Deprecated: Implement ``get_schema_for_op`` instead to provide
        accurate per-operation schemas.
        """
        ...

    def get_schema_for_op(self, op: str) -> dict[str, Any] | None:
        """Return the JSON schema for a specific operation.

        Returns ``None`` if the plugin cannot provide a schema for *op*.
        Preferred over the deprecated ``get_schema`` method.
        """
        ...

    def get_output_schema(self) -> dict[str, Any] | None:
        """Return a JSON schema for PluginResult.data when status == 'success' (optional)."""
        ...

    async def execute(self, params: dict[str, Any], context: ExecuteContext, host: Any) -> PluginResult: ...
