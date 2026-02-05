from __future__ import annotations

import logging

from .base import ImmutableCapabilityMixin
from .storage_capability import StorageCapability

logger = logging.getLogger(__name__)


class CursorCapability(ImmutableCapabilityMixin):
    """Standardized per-feed cursor storage scoped by (schedule_id, kb_id).

    Delegates to StorageCapability with namespace='cursor'.
    Key shape is host-managed; plugins never construct keys.
    When schedule_id is absent (ad-hoc/manual runs), uses an 'adhoc' scope.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' cursors.
    """

    __slots__ = ("_plugin_name", "_schedule_id", "_storage", "_user_id")
    NAMESPACE = "cursor"

    _plugin_name: str
    _user_id: str
    _schedule_id: str | None
    _storage: StorageCapability

    def __init__(self, *, plugin_name: str, user_id: str, schedule_id: str | None = None):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_schedule_id", str(schedule_id) if schedule_id else None)
        object.__setattr__(self, "_storage", StorageCapability(plugin_name=plugin_name, user_id=user_id))

    def _key(self, kb_id: str) -> str:
        sid = self._schedule_id
        return f"feed:{sid}:kb:{kb_id}" if sid else f"adhoc:kb:{kb_id}"

    async def get(self, kb_id: str) -> str | None:
        """Get the cursor value for a knowledge base.

        Returns None if the cursor doesn't exist or on any error.
        Errors are logged for observability.

        Args:
            kb_id: The knowledge base ID to get the cursor for.

        Returns:
            The cursor value as a string, or None if not found or on error.

        """
        try:
            val = await self._storage.get(self._key(kb_id), namespace=self.NAMESPACE)
            # Stored as cursor_string or {"value": cursor_string}
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                v = val.get("value")
                return v if isinstance(v, str) else None
            return None
        except Exception as e:
            logger.warning(
                f"CursorCapability.get failed for kb_id '{kb_id}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "kb_id": kb_id,
                    "error": str(e),
                }
            )
            return None

    async def set(self, kb_id: str, value: str) -> None:
        await self._storage.put(self._key(kb_id), value, namespace=self.NAMESPACE)

    async def set_safe(self, kb_id: str, value: str) -> bool:
        """Sets the cursor value, returning success status instead of raising.

        This is a convenience method for plugins that want to avoid try/except
        blocks. On error, logs a warning and returns False.

        Args:
            kb_id: The knowledge base ID to set the cursor for.
            value: The cursor value to store.

        Returns:
            True if the cursor was set successfully, False on any error.

        """
        try:
            await self.set(kb_id, value)
            return True
        except Exception as e:
            logger.warning(
                f"CursorCapability.set_safe failed for kb_id '{kb_id}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "kb_id": kb_id,
                    "error": str(e),
                }
            )
            return False

    async def delete(self, kb_id: str) -> None:
        await self._storage.delete(self._key(kb_id), namespace=self.NAMESPACE)

    async def delete_safe(self, kb_id: str) -> bool:
        """Deletes the cursor, returning success status instead of raising.

        This is a convenience method for plugins that want to avoid try/except
        blocks. On error, logs a warning and returns False.

        Args:
            kb_id: The knowledge base ID to delete the cursor for.

        Returns:
            True if the cursor was deleted successfully, False on any error.

        """
        try:
            await self.delete(kb_id)
            return True
        except Exception as e:
            logger.warning(
                f"CursorCapability.delete_safe failed for kb_id '{kb_id}': {e}",
                extra={
                    "plugin_name": self._plugin_name,
                    "user_id": self._user_id,
                    "kb_id": kb_id,
                    "error": str(e),
                }
            )
            return False

