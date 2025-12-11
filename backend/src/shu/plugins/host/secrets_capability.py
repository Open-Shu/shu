from __future__ import annotations

import logging
from typing import Any

from ...core.oauth_encryption import get_oauth_encryption_service
from .base import ImmutableCapabilityMixin
from ._storage_ops import (
    storage_get,
    storage_get_system,
    storage_set,
    storage_delete,
)

logger = logging.getLogger(__name__)


class SecretsCapability(ImmutableCapabilityMixin):
    """Encrypted KV store for credentials/secrets scoped per user and plugin.

    Backed by PluginStorage with ``namespace='secret'``. Values are encrypted
    with the OAuth encryption service to avoid plaintext at rest.

    Lookup semantics:

    * Writes via this capability are always *user scoped* (per user+plugin).
    * Reads first check the user scope; if no value is present, they fall back
      to any configured *system scoped* secret for the same plugin+key.

    Operation-level policies (for example, requiring user-only secrets vs
    allowing system-or-user) are enforced by higher-level identity/secret
    preflight logic; this helper focuses only on lookup order.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' secrets.
    """

    __slots__ = ("_plugin_name", "_user_id", "_enc")
    NAMESPACE = "secret"

    _plugin_name: str
    _user_id: str
    _enc: Any

    def __init__(self, *, plugin_name: str, user_id: str):
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_enc", get_oauth_encryption_service())

    async def set(self, key: str, value: str) -> None:
        enc = self._enc.encrypt_token(value)
        await storage_set(
            self._user_id, self._plugin_name, self.NAMESPACE, key, {"v": enc}
        )
        logger.info(
            "host.secrets.set",
            extra={"plugin": self._plugin_name, "user_id": self._user_id, "key": key},
        )

    async def get(self, key: str) -> Any | None:
        """Get a decrypted secret value with user→system fallback.

        Resolution order:

        1. User-scoped secret for (user_id, plugin_name, key)
        2. System-scoped secret for (plugin_name, key)
        """

        # User-scoped first
        raw = await storage_get(self._user_id, self._plugin_name, self.NAMESPACE, key)
        if not raw:
            # Fallback to system scope if no user value present
            raw = await storage_get_system(self._plugin_name, self.NAMESPACE, key)
            if not raw:
                return None
        enc = raw.get("v")
        if not enc:
            return None
        try:
            return self._enc.decrypt_token(enc)
        except Exception:
            logger.warning(
                "Failed to decrypt secret",
                extra={"plugin": self._plugin_name, "user_id": self._user_id, "key": key},
            )
            return None

    async def delete(self, key: str) -> None:
        await storage_delete(self._user_id, self._plugin_name, self.NAMESPACE, key)
        logger.info(
            "host.secrets.delete",
            extra={"plugin": self._plugin_name, "user_id": self._user_id, "key": key},
        )

