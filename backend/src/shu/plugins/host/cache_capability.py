from __future__ import annotations

import json
from typing import Any

from ...core.database import get_redis_client
from .base import ImmutableCapabilityMixin


class CacheCapability(ImmutableCapabilityMixin):
    """Redis-backed transient KV with TTL (per user+plugin namespace).

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' cache.
    """

    __slots__ = ("_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str

    def __init__(self, *, plugin_name: str, user_id: str):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        client = await get_redis_client()
        namespaced = f"tool_cache:{self._plugin_name}:{self._user_id}:{key}"
        try:
            await client.set(namespaced, json.dumps(value, default=str), ex=max(1, int(ttl_seconds)))
        except Exception:
            # Best-effort; do not crash tool
            pass

    async def get(self, key: str):
        client = await get_redis_client()
        namespaced = f"tool_cache:{self._plugin_name}:{self._user_id}:{key}"
        try:
            raw = await client.get(namespaced)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    async def delete(self, key: str) -> None:
        client = await get_redis_client()
        namespaced = f"tool_cache:{self._plugin_name}:{self._user_id}:{key}"
        try:
            await client.delete(namespaced)
        except Exception:
            pass

