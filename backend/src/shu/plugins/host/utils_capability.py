"""UtilsCapability - Plugin utility functions.

This module provides utility functions for plugins that help with common
patterns like batch processing with error handling.

Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
plugins from mutating internal state.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .base import ImmutableCapabilityMixin

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class UtilsCapability(ImmutableCapabilityMixin):
    """Plugin utility functions for common patterns.

    Provides helper functions that reduce boilerplate in plugins, particularly
    for batch operations where individual failures should not stop the whole
    operation.

    This capability is always available to plugins (no declaration required).

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating internal state.

    Example:
        # In a plugin - process messages with error handling
        async def fetch_message(mid):
            return await host.http.fetch("GET", f"{api}/messages/{mid}")

        messages, errors = await host.utils.map_safe(message_ids, fetch_message)
        if errors:
            host.log.warning(f"Failed to fetch {len(errors)} messages")
        for msg in messages:
            # process message...

    """

    __slots__ = ("_plugin_name", "_user_id")

    _plugin_name: str
    _user_id: str

    def __init__(self, *, plugin_name: str, user_id: str) -> None:
        """Initialize the utils capability.

        Args:
            plugin_name: The name of the plugin using this capability.
            user_id: The ID of the user the plugin is running for.

        """
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)

    async def map_safe(
        self,
        items: list[T],
        async_fn: Callable[[T], Awaitable[R]],
        *,
        max_errors: int | None = None,
    ) -> tuple[list[R], list[tuple[T, Exception]]]:
        """Process items with a function, collecting errors instead of failing.

        This is useful for batch operations where individual failures should
        not stop the whole operation. Instead of try/except in a loop, use
        this to get all results and errors in one call.

        Args:
            items: List of items to process.
            async_fn: Async function to apply to each item.
            max_errors: Optional maximum number of errors before stopping.
                Must be None (unlimited) or a positive integer >= 1.
                If None (default), all items are processed regardless of errors.
                If set, processing stops after this many errors.

        Returns:
            A tuple of (results, errors) where:
            - results: List of successful results (in order of success)
            - errors: List of (item, exception) tuples for failed items

        Raises:
            ValueError: If max_errors is not None and less than 1.

        Example:
            async def fetch_user(user_id):
                resp = await host.http.fetch("GET", f"{api}/users/{user_id}")
                return resp["body"]

            users, errors = await host.utils.map_safe(user_ids, fetch_user)
            if errors:
                host.log.warning(f"Failed to fetch {len(errors)} users")
            for user in users:
                print(user["name"])

        """
        if max_errors is not None and max_errors < 1:
            raise ValueError("max_errors must be None or >= 1")

        results: list[R] = []
        errors: list[tuple[T, Exception]] = []

        for item in items:
            if max_errors is not None and len(errors) >= max_errors:
                # Stop processing if we've hit the error limit
                break
            try:
                result = await async_fn(item)
                results.append(result)
            except Exception as e:
                errors.append((item, e))

        return results, errors

    async def filter_safe(
        self,
        items: list[T],
        async_predicate: Callable[[T], Awaitable[bool]],
    ) -> tuple[list[T], list[tuple[T, Exception]]]:
        """Filter items with a predicate, collecting errors instead of failing.

        This is useful for filtering operations where individual failures
        should not stop the whole operation.

        Args:
            items: List of items to filter.
            async_predicate: Async function that returns True to keep an item.

        Returns:
            A tuple of (kept_items, errors) where:
            - kept_items: List of items where predicate returned True
            - errors: List of (item, exception) tuples for failed predicates

        Example:
            async def is_valid(item):
                resp = await host.http.fetch("GET", f"{api}/validate/{item['id']}")
                return resp["body"]["valid"]

            valid_items, errors = await host.utils.filter_safe(items, is_valid)

        """
        kept: list[T] = []
        errors: list[tuple[T, Exception]] = []

        for item in items:
            try:
                if await async_predicate(item):
                    kept.append(item)
            except Exception as e:
                errors.append((item, e))

        return kept, errors
