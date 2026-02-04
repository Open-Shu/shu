"""CacheCapability - Plugin cache capability using unified CacheBackend.

This module provides a cache capability for plugins that uses the unified
CacheBackend interface. It supports both Redis and in-memory backends
transparently based on configuration.

Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
plugins from mutating _plugin_name or _user_id to access other plugins' cache.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...core.cache_backend import CacheBackend, get_cache_backend
from .base import ImmutableCapabilityMixin

logger = logging.getLogger(__name__)


class CacheCapability(ImmutableCapabilityMixin):
    """Plugin cache capability with namespace isolation.

    Provides key-value caching with TTL support for plugins. Each plugin
    gets its own namespace to prevent key collisions between plugins.

    The cache backend is selected automatically based on configuration:
    - If SHU_REDIS_URL is set and Redis is reachable -> Redis backend
    - Otherwise -> In-memory backend

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' cache.

    Namespace Format:
        Keys are namespaced as: tool_cache:{plugin_name}:{user_id}:{key}
        This ensures complete isolation between plugins and users.

    Error Handling:
        All operations handle errors gracefully without crashing the plugin.
        On error, set() silently fails, get() returns None, delete() silently fails.

    Example:
        # In a plugin
        async def my_tool(host):
            # Store a value with 5-minute TTL
            await host.cache.set("last_sync", {"timestamp": "2024-01-01"}, ttl_seconds=300)

            # Retrieve the value
            data = await host.cache.get("last_sync")
            if data:
                print(f"Last sync: {data['timestamp']}")

            # Delete the value
            await host.cache.delete("last_sync")

    """

    __slots__ = ("_backend", "_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str
    _backend: CacheBackend | None

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        backend: CacheBackend | None = None,
    ):
        """Initialize the cache capability.

        Args:
            plugin_name: The name of the plugin using this capability.
            user_id: The ID of the user the plugin is running for.
            backend: Optional CacheBackend instance for dependency injection.
                If not provided, the backend will be obtained lazily via
                get_cache_backend() on first use.

        """
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_backend", backend)

    def _make_namespaced_key(self, key: str) -> str:
        """Create a namespaced cache key.

        Args:
            key: The user-provided key.

        Returns:
            The namespaced key in format: tool_cache:{plugin_name}:{user_id}:{key}

        """
        return f"tool_cache:{self._plugin_name}:{self._user_id}:{key}"

    async def _get_backend(self) -> CacheBackend:
        """Get the cache backend, initializing if necessary.

        Returns:
            The CacheBackend instance.

        """
        if self._backend is None:
            backend = await get_cache_backend()
            object.__setattr__(self, "_backend", backend)
        return self._backend

    async def _serialize_and_set(
        self, namespaced_key: str, value: Any, ttl_seconds: int
    ) -> None:
        """Serialize value and write to cache backend.
        
        This is the shared implementation for set() and set_safe().
        Exceptions are not caught here - callers handle errors differently.
        
        Args:
            namespaced_key: The fully namespaced cache key.
            value: The value to store (must be JSON-serializable).
            ttl_seconds: Time-to-live in seconds.

        """
        backend = await self._get_backend()
        serialized = json.dumps(value, default=str)
        await backend.set(namespaced_key, serialized, ttl_seconds=max(1, int(ttl_seconds)))

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Store a value in the cache with optional TTL.

        The value is JSON-serialized before storage. Complex objects are
        converted to strings using the default=str fallback.

        Args:
            key: The cache key (will be namespaced automatically).
            value: The value to store (must be JSON-serializable).
            ttl_seconds: Time-to-live in seconds. Default is 300 (5 minutes).
                Must be at least 1 second.

        Note:
            This method handles errors gracefully and will not raise exceptions.
            On error, the operation silently fails and a warning is logged.

        Example:
            await cache.set("user_prefs", {"theme": "dark"}, ttl_seconds=3600)

        """
        namespaced_key = self._make_namespaced_key(key)
        try:
            await self._serialize_and_set(namespaced_key, value, ttl_seconds)
        except Exception as e:
            # Best-effort; do not crash tool
            logger.warning(
                f"CacheCapability.set failed for key '{key}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "key": key,
                    "error": str(e),
                },
            )

    async def get(self, key: str) -> Any:
        """Retrieve a value from the cache.

        The value is JSON-deserialized after retrieval.

        Args:
            key: The cache key (will be namespaced automatically).

        Returns:
            The cached value (deserialized from JSON), or None if the key
            does not exist, has expired, or an error occurred.

        Note:
            This method handles errors gracefully and will not raise exceptions.
            On error, None is returned and a warning is logged.

        Example:
            prefs = await cache.get("user_prefs")
            if prefs:
                print(f"Theme: {prefs['theme']}")

        """
        namespaced_key = self._make_namespaced_key(key)
        try:
            backend = await self._get_backend()
            raw = await backend.get(namespaced_key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            # Best-effort; do not crash tool
            logger.warning(
                f"CacheCapability.get failed for key '{key}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "key": key,
                    "error": str(e),
                },
            )
            return None

    async def delete(self, key: str) -> None:
        """Delete a value from the cache.

        Args:
            key: The cache key (will be namespaced automatically).

        Note:
            This method handles errors gracefully and will not raise exceptions.
            On error, the operation silently fails and a warning is logged.

        Example:
            await cache.delete("user_prefs")

        """
        namespaced_key = self._make_namespaced_key(key)
        try:
            backend = await self._get_backend()
            await backend.delete(namespaced_key)
        except Exception as e:
            # Best-effort; do not crash tool
            logger.warning(
                f"CacheCapability.delete failed for key '{key}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "key": key,
                    "error": str(e),
                },
            )

    async def set_safe(self, key: str, value: Any, ttl_seconds: int = 300) -> bool:
        """Store a value in the cache, returning success status.

        This is similar to set() but returns a boolean indicating success.
        On error, logs a warning and returns False instead of silently failing.

        Args:
            key: The cache key (will be namespaced automatically).
            value: The value to store (must be JSON-serializable).
            ttl_seconds: Time-to-live in seconds. Default is 300 (5 minutes).

        Returns:
            True if the value was stored successfully, False on any error.

        Example:
            success = await cache.set_safe("user_prefs", {"theme": "dark"})
            if not success:
                host.log.warning("Failed to cache user preferences")

        """
        namespaced_key = self._make_namespaced_key(key)
        try:
            await self._serialize_and_set(namespaced_key, value, ttl_seconds)
            return True
        except Exception as e:
            logger.warning(
                f"CacheCapability.set_safe failed for key '{key}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "key": key,
                    "error": str(e),
                }
            )
            return False

    async def delete_safe(self, key: str) -> bool:
        """Delete a value from the cache, returning success status.

        This is similar to delete() but returns a boolean indicating success.

        Args:
            key: The cache key (will be namespaced automatically).

        Returns:
            True if the value was deleted successfully, False on any error.

        Example:
            success = await cache.delete_safe("user_prefs")

        """
        namespaced_key = self._make_namespaced_key(key)
        try:
            backend = await self._get_backend()
            await backend.delete(namespaced_key)
            return True
        except Exception as e:
            logger.warning(
                f"CacheCapability.delete_safe failed for key '{key}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "key": key,
                    "error": str(e),
                }
            )
            return False
