"""Admin-facing helpers for managing encrypted plugin secrets.

Provides CRUD operations for plugin secrets with encryption. Secrets are
stored in the ``plugin_storage`` table with ``namespace='secret'`` and
encrypted using the OAuth encryption service.

Scopes:
- ``"user"``: Per-user secrets keyed by (user_id, plugin_name, key).
- ``"system"``: Shared secrets keyed by (plugin_name, key); user_id is for audit only.

This module is a thin service layer over ``_storage_ops`` that adds encryption.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..core.oauth_encryption import get_oauth_encryption_service
from ..plugins.host._storage_ops import (
    normalize_scope,
    storage_delete_scoped,
    storage_get_scoped,
    storage_list_keys,
    storage_list_meta,
    storage_purge_old,
    storage_set_scoped,
)

SECRET_NAMESPACE = "secret"  # noqa: S105 # not an actual secret


async def list_secret_keys(
    name: str,
    *,
    user_id: str | None,
    scope: str = "user",
) -> list[str]:
    """Return list of secret keys for a plugin and scope.

    For user scope, ``user_id`` is required; for system scope, it is ignored.
    """
    return await storage_list_keys(name, SECRET_NAMESPACE, scope=normalize_scope(scope), user_id=user_id)


async def list_secrets_meta(
    name: str,
    *,
    user_id: str | None,
    scope: str = "user",
) -> list[tuple[str, datetime]]:
    """Return list of (key, updated_at) for a plugin and scope."""
    return await storage_list_meta(name, SECRET_NAMESPACE, scope=normalize_scope(scope), user_id=user_id)


async def get_secret(
    name: str,
    key: str,
    *,
    user_id: str | None,
    scope: str = "user",
) -> str | None:
    """Get a decrypted secret value.

    For user scope, ``user_id`` is required; for system scope, it is ignored.
    Returns the decrypted value or None if not found.
    """
    raw = await storage_get_scoped(name, SECRET_NAMESPACE, key, scope=normalize_scope(scope), user_id=user_id)
    if not raw:
        return None
    enc = raw.get("v")
    if not enc:
        return None
    return get_oauth_encryption_service().decrypt_token(enc)


async def set_secret(
    name: str,
    key: str,
    *,
    user_id: str,
    value: str,
    scope: str = "user",
) -> None:
    """Create or update an encrypted secret value.

    For ``scope='user'`` this writes a per-user row. For ``scope='system'`` it
    writes/updates a shared row keyed only by (plugin_name, key) while still
    recording ``user_id`` for audit.
    """
    enc = get_oauth_encryption_service().encrypt_token(value)
    await storage_set_scoped(
        name,
        SECRET_NAMESPACE,
        key,
        {"v": enc},
        scope=normalize_scope(scope),
        user_id=user_id,
    )


async def delete_secret(
    name: str,
    key: str,
    *,
    user_id: str | None,
    scope: str = "user",
) -> None:
    """Delete a secret for the given scope.

    For user scope, ``user_id`` is required. For system scope, the row is
    deleted regardless of the stored user id.
    """
    s = normalize_scope(scope)
    if s == "user" and not user_id:
        return
    await storage_delete_scoped(name, SECRET_NAMESPACE, key, scope=s, user_id=user_id)


async def purge_old_secrets(
    name: str,
    *,
    user_id: str | None,
    older_than_days: int,
    scope: str = "user",
) -> int:
    """Purge secrets older than ``older_than_days`` for the given scope.

    For user scope, ``user_id`` is required. System scope ignores ``user_id``.
    Returns the number of deleted rows.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(older_than_days)))
    return await storage_purge_old(
        name,
        SECRET_NAMESPACE,
        cutoff,
        scope=normalize_scope(scope),
        user_id=user_id,
    )
