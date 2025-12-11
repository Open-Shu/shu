from __future__ import annotations

import json
from typing import Any, Optional

from ...core.config import get_settings_instance
from .base import ImmutableCapabilityMixin
from ._storage_ops import storage_get, storage_set, storage_delete


class StorageCapability(ImmutableCapabilityMixin):
    """Small-object JSON storage (per user+plugin). Not for large blobs yet.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' storage.
    """

    __slots__ = ("_plugin_name", "_user_id", "_max_bytes")
    NAMESPACE = "storage"

    _plugin_name: str
    _user_id: str
    _max_bytes: int

    def __init__(self, *, plugin_name: str, user_id: str):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        s = get_settings_instance()
        object.__setattr__(self, "_max_bytes", int(getattr(s, "tool_storage_object_max_bytes", 64 * 1024)))

    async def put(self, key: str, value: Any, *, namespace: Optional[str] = None) -> None:
        """Store a value. Optionally override namespace."""
        ns = namespace or self.NAMESPACE
        try:
            payload = json.dumps(value, default=str)
        except Exception:
            payload = json.dumps({"value": value}, default=str)
        if self._max_bytes and len(payload.encode("utf-8")) > self._max_bytes:
            raise ValueError(f"storage object too large ({len(payload)} > {self._max_bytes})")

        await storage_set(
            self._user_id, self._plugin_name, ns, key, {"json": json.loads(payload)}
        )

    async def get(self, key: str, *, namespace: Optional[str] = None) -> Optional[Any]:
        """Retrieve a value. Optionally override namespace."""
        ns = namespace or self.NAMESPACE
        raw = await storage_get(self._user_id, self._plugin_name, ns, key)
        return raw.get("json") if raw else None

    async def delete(self, key: str, *, namespace: Optional[str] = None) -> None:
        """Delete a value. Optionally override namespace."""
        ns = namespace or self.NAMESPACE
        await storage_delete(self._user_id, self._plugin_name, ns, key)

