"""Low-level CRUD operations for PluginStorage.

Provides get/set/delete/list operations for plugin storage entries. All
capability classes and services that use PluginStorage should delegate
to these functions to centralize query logic and session management.

Storage entries are scoped by:
- ``scope``: "user" (per-user) or "system" (shared across users)
- ``user_id``: owner for user scope; audit trail for system scope
- ``plugin_name``: the plugin identifier
- ``namespace``: logical grouping (e.g., "secret", "state", "cursor")
- ``key``: the specific entry key

Parameter Ordering Convention:
    This module has two function families with different parameter orderings:

    1. Scoped functions (``*_scoped``) - scope-first ordering:
       ``(plugin_name, namespace, key, ..., scope=, user_id=)``
       These are the primary API for new code.

    2. Legacy wrapper functions - user-first ordering:
       ``(user_id, plugin_name, namespace, key, ...)``
       These exist for backward compatibility with capability classes.

    When adding new functions, prefer the scoped pattern with scope as a
    keyword argument.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.database import get_db_session
from ...models.plugin_storage import PluginStorage

logger = logging.getLogger(__name__)


def normalize_scope(scope: str | None) -> str:
    """Normalize scope to 'user' or 'system'.

    This is the canonical scope normalizer - import and use this rather than
    duplicating the logic in other modules.
    """
    scope_lower = (scope or "user").strip().lower()
    return "system" if scope_lower == "system" else "user"


def _build_where_clause(
    scope: str,
    plugin_name: str,
    namespace: str,
    key: str | None = None,
    user_id: str | None = None,
):
    """Build WHERE conditions for storage queries.

    For user scope, user_id is part of the key. For system scope, user_id is
    ignored in queries (it's only stored for audit).
    """
    conditions = [
        PluginStorage.scope == scope,
        PluginStorage.plugin_name == plugin_name,
        PluginStorage.namespace == namespace,
    ]
    if scope == "user" and user_id:
        conditions.append(PluginStorage.user_id == user_id)
    if key is not None:
        conditions.append(PluginStorage.key == key)
    return conditions


async def _close_session(db: AsyncSession, context: str) -> None:
    """Close a db session, logging any errors."""
    try:
        await db.close()
    except Exception as e:
        logger.warning(
            "Failed to close db session",
            extra={"context": context, "error": str(e)},
        )


# ---------------------------------------------------------------------------
# Core CRUD with scope parameter
# ---------------------------------------------------------------------------


async def storage_get_scoped(
    plugin_name: str,
    namespace: str,
    key: str,
    *,
    scope: str = "user",
    user_id: str | None = None,
) -> dict | None:
    """Retrieve a storage entry's value dict.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace (e.g., "secret").
        key: Entry key.
        scope: "user" or "system".
        user_id: Required for user scope; ignored for system scope.

    Returns:
        The stored value dict, or None if not found.

    """
    normalized_scope = normalize_scope(scope)
    db = await get_db_session()
    try:
        conditions = _build_where_clause(normalized_scope, plugin_name, namespace, key, user_id)
        res = await db.execute(select(PluginStorage).where(*conditions))
        row = res.scalars().first()
        return row.value if row and row.value else None  # type: ignore[return-value]
    finally:
        await _close_session(db, "storage_get_scoped")


async def storage_set_scoped(
    plugin_name: str,
    namespace: str,
    key: str,
    value: dict,
    *,
    scope: str = "user",
    user_id: str,
) -> None:
    """Create or update a storage entry.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace.
        key: Entry key.
        value: Value dict to store.
        scope: "user" or "system".
        user_id: Owner for user scope; audit trail for system scope.

    """
    normalized_scope = normalize_scope(scope)
    db = await get_db_session()
    try:
        conditions = _build_where_clause(
            normalized_scope,
            plugin_name,
            namespace,
            key,
            user_id if normalized_scope == "user" else None,
        )
        res = await db.execute(select(PluginStorage).where(*conditions))
        existing = res.scalars().first()

        if existing:
            existing.value = value  # type: ignore[assignment]
            existing.user_id = user_id  # type: ignore[assignment] # Update audit trail
        else:
            row = PluginStorage(
                scope=normalized_scope,
                user_id=user_id,
                plugin_name=plugin_name,
                namespace=namespace,
                key=key,
                value=value,
            )
            db.add(row)

        await db.commit()
    finally:
        await _close_session(db, "storage_set_scoped")


async def storage_delete_scoped(
    plugin_name: str,
    namespace: str,
    key: str,
    *,
    scope: str = "user",
    user_id: str | None = None,
) -> None:
    """Delete a storage entry.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace.
        key: Entry key.
        scope: "user" or "system".
        user_id: Required for user scope; ignored for system scope.

    """
    normalized_scope = normalize_scope(scope)
    db = await get_db_session()
    try:
        conditions = _build_where_clause(normalized_scope, plugin_name, namespace, key, user_id)
        await db.execute(sa_delete(PluginStorage).where(*conditions))
        await db.commit()
    finally:
        await _close_session(db, "storage_delete_scoped")


async def storage_list_keys(
    plugin_name: str,
    namespace: str,
    *,
    scope: str = "user",
    user_id: str | None = None,
) -> list[str]:
    """List all keys for a plugin/namespace/scope.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace.
        scope: "user" or "system".
        user_id: Required for user scope; ignored for system scope.

    Returns:
        List of key names.

    """
    normalized_scope = normalize_scope(scope)
    if normalized_scope == "user" and not user_id:
        return []
    db = await get_db_session()
    try:
        conditions = _build_where_clause(normalized_scope, plugin_name, namespace, key=None, user_id=user_id)
        res = await db.execute(select(PluginStorage.key).where(*conditions))
        return [row[0] for row in res.all()]
    finally:
        await _close_session(db, "storage_list_keys")


async def storage_list_meta(
    plugin_name: str,
    namespace: str,
    *,
    scope: str = "user",
    user_id: str | None = None,
) -> list[tuple[str, datetime]]:
    """List (key, updated_at) tuples for a plugin/namespace/scope.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace.
        scope: "user" or "system".
        user_id: Required for user scope; ignored for system scope.

    Returns:
        List of (key, updated_at) tuples.

    """
    normalized_scope = normalize_scope(scope)
    if normalized_scope == "user" and not user_id:
        return []
    db = await get_db_session()
    try:
        conditions = _build_where_clause(normalized_scope, plugin_name, namespace, key=None, user_id=user_id)
        res = await db.execute(select(PluginStorage.key, PluginStorage.updated_at).where(*conditions))
        return [(r[0], r[1]) for r in res.all()]
    finally:
        await _close_session(db, "storage_list_meta")


async def storage_purge_old(
    plugin_name: str,
    namespace: str,
    cutoff: datetime,
    *,
    scope: str = "user",
    user_id: str | None = None,
) -> int:
    """Delete entries older than cutoff for a plugin/namespace/scope.

    Args:
        plugin_name: Plugin identifier.
        namespace: Logical namespace.
        cutoff: Delete entries with updated_at before this timestamp.
        scope: "user" or "system".
        user_id: Required for user scope; ignored for system scope.

    Returns:
        Number of deleted rows.

    """
    normalized_scope = normalize_scope(scope)
    if normalized_scope == "user" and not user_id:
        return 0
    db = await get_db_session()
    try:
        conditions = _build_where_clause(normalized_scope, plugin_name, namespace, key=None, user_id=user_id)
        conditions.append(PluginStorage.updated_at < cutoff)
        res = await db.execute(sa_delete(PluginStorage).where(*conditions))
        await db.commit()
        return int(getattr(res, "rowcount", 0) or 0)
    finally:
        await _close_session(db, "storage_purge_old")


# ---------------------------------------------------------------------------
# Convenience wrappers for backward compatibility and clarity
# ---------------------------------------------------------------------------


async def storage_get(
    user_id: str,
    plugin_name: str,
    namespace: str,
    key: str,
) -> dict | None:
    """Get a user-scoped storage entry."""
    return await storage_get_scoped(plugin_name, namespace, key, scope="user", user_id=user_id)


async def storage_set(
    user_id: str,
    plugin_name: str,
    namespace: str,
    key: str,
    value: dict,
) -> None:
    """Set a user-scoped storage entry."""
    await storage_set_scoped(plugin_name, namespace, key, value, scope="user", user_id=user_id)


async def storage_delete(
    user_id: str,
    plugin_name: str,
    namespace: str,
    key: str,
) -> None:
    """Delete a user-scoped storage entry."""
    await storage_delete_scoped(plugin_name, namespace, key, scope="user", user_id=user_id)


async def storage_get_system(
    plugin_name: str,
    namespace: str,
    key: str,
) -> dict | None:
    """Get a system-scoped storage entry."""
    return await storage_get_scoped(plugin_name, namespace, key, scope="system")
