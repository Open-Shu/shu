"""
Plugin interface v0 for Agent Foundation MVP.

The registry maps plugin names to callables implementing this interface.
"""
from __future__ import annotations
from typing import Any, Dict, Optional, Protocol, Type
from pydantic import BaseModel


class PluginInput(BaseModel):
    # flexible payload for v0
    params: Dict[str, Any] = {}


class PluginResult(BaseModel):
    ok: bool
    name: str
    summary: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Plugin(Protocol):
    name: str
    # Optional: plugins may provide a Pydantic model for their input params.
    # When present, the runner will validate/coerce params before execution.
    input_model: Optional[Type[BaseModel]]  # type: ignore[assignment]

    async def execute(self, *, user_id: str, agent_key: str, payload: PluginInput) -> PluginResult:
        ...

