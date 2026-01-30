"""Base classes and utilities for plugin host capabilities.

This module provides common patterns used across capability classes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class StorageScope(str, Enum):
    """Storage scope for plugin data.

    - USER: Per-user storage keyed by (user_id, plugin_name, namespace, key).
    - SYSTEM: Shared storage keyed by (plugin_name, namespace, key); user_id for audit only.
    """

    USER = "user"
    SYSTEM = "system"


class ImmutableCapabilityMixin:
    """Mixin that makes a __slots__ class immutable after __init__.

    Classes using this mixin must:
    1. Define __slots__ for all instance attributes
    2. Use object.__setattr__ in __init__ to set attributes

    Security: Prevents plugins from mutating capability attributes (e.g., _user_id,
    _plugin_name) to impersonate other users or bypass access controls.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{self.__class__.__name__} attributes are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{self.__class__.__name__} attributes are immutable")


def normalize_storage_scope(scope: str | None) -> StorageScope:
    """Normalize scope string to StorageScope enum.

    This is an alternative to the string-based normalize_scope() that returns
    a typed enum value.
    """
    scope_lower = (scope or "user").strip().lower()
    return StorageScope.SYSTEM if scope_lower == "system" else StorageScope.USER
