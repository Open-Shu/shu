"""LogCapability - Plugin logging capability.

This module provides a logging capability for plugins that integrates with
the Shu logging infrastructure. It allows plugins to emit structured logs
with plugin context automatically included.

Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
plugins from mutating _plugin_name or _user_id to spoof log entries.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ImmutableCapabilityMixin

# Use a dedicated logger for plugin logs
plugin_logger = logging.getLogger("shu.plugins.runtime")


class LogCapability(ImmutableCapabilityMixin):
    """Plugin logging capability with automatic context injection.

    Provides structured logging for plugins. Each log entry automatically
    includes the plugin name, user ID, and optional operation context.

    This capability is always available to plugins (no declaration required)
    to encourage proper logging over silent exception swallowing.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to spoof log entries.

    Example:
        # In a plugin
        async def my_tool(host):
            host.log.info("Starting sync")

            try:
                result = await do_something()
            except Exception as e:
                host.log.error(f"Sync failed: {e}")
                raise

            host.log.info(f"Sync complete: {result['count']} items")

    """

    __slots__ = ("_operation", "_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str
    _operation: str | None

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        operation: str | None = None,
    ) -> None:
        """Initialize the log capability.

        Args:
            plugin_name: The name of the plugin using this capability.
            user_id: The ID of the user the plugin is running for.
            operation: Optional operation name for additional context.

        """
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_operation", operation)

    def _make_extra(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create the extra dict with plugin context.

        Args:
            extra: Optional additional context to include.

        Returns:
            Dict with plugin context merged with any extra context.

        Note:
            Protected fields (plugin_name, user_id, operation) are set after
            merging extra to prevent plugins from spoofing log context.

        """
        base: dict[str, Any] = {}
        if extra:
            base.update(extra)
        # Set protected fields after merging extra to prevent spoofing
        base["plugin_name"] = self._plugin_name
        base["user_id"] = self._user_id
        if self._operation:
            base["operation"] = self._operation
        return base

    def debug(self, msg: str, *, extra: dict[str, Any] | None = None) -> None:
        """Log a debug message.

        Args:
            msg: The message to log.
            extra: Optional additional context to include.

        """
        plugin_logger.debug(msg, extra=self._make_extra(extra))

    def info(self, msg: str, *, extra: dict[str, Any] | None = None) -> None:
        """Log an info message.

        Args:
            msg: The message to log.
            extra: Optional additional context to include.

        """
        plugin_logger.info(msg, extra=self._make_extra(extra))

    def warning(self, msg: str, *, extra: dict[str, Any] | None = None) -> None:
        """Log a warning message.

        Args:
            msg: The message to log.
            extra: Optional additional context to include.

        """
        plugin_logger.warning(msg, extra=self._make_extra(extra))

    def error(self, msg: str, *, extra: dict[str, Any] | None = None) -> None:
        """Log an error message.

        Args:
            msg: The message to log.
            extra: Optional additional context to include.

        """
        plugin_logger.error(msg, extra=self._make_extra(extra))

    def exception(self, msg: str, *, extra: dict[str, Any] | None = None) -> None:
        """Log an error message with exception info.

        This should be called from within an exception handler to include
        the traceback in the log entry.

        Args:
            msg: The message to log.
            extra: Optional additional context to include.

        """
        plugin_logger.exception(msg, extra=self._make_extra(extra))
