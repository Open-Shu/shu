"""Plugin Storage model for plugin runtime state.

Provides key/value storage for plugins scoped by
``(scope, user_id, plugin_name, namespace, key)``.

Separates plugin state (cursors, secrets, storage) from agent memory and
supports both per-user and system-wide entries via the ``scope`` column.
"""

from sqlalchemy import JSON, Column, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import BaseModel


class PluginStorage(BaseModel):
    """Key/value storage scoped to (scope, user_id, plugin_name, namespace, key).

    scope: Logical scope for the entry. Currently ``"user"`` (per-user) or
           ``"system"`` (system-wide). Existing rows default to ``"user"``.
    user_id: The Shu user who owns this storage entry. For scheduled feeds
             (including domain-wide delegation), this is the feed owner, not
             the external service account user being accessed. For
             system-scoped rows this typically records the admin who created
             the entry and is not used for lookups.
    plugin_name: The plugin identifier (e.g., "gdrive_files", "gmail_feed").
    namespace: One of 'storage', 'secret', or 'cursor'.
    key: Arbitrary key within the (scope, user, plugin, namespace) scope.
    value: JSON payload.
    """

    __tablename__ = "plugin_storage"

    scope = Column(String(10), nullable=False, index=True, default="user")

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plugin_name = Column(String(100), nullable=False, index=True)
    namespace = Column(String(50), nullable=False, index=True)
    key = Column(String(200), nullable=False, index=True)
    value = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "scope",
            "user_id",
            "plugin_name",
            "namespace",
            "key",
            name="uq_plugin_storage_scope_key",
        ),
        Index(
            "ix_plugin_storage_lookup",
            "scope",
            "user_id",
            "plugin_name",
            "namespace",
            "key",
        ),
    )

    user = relationship("User", backref="plugin_storage_entries")

    def __repr__(self) -> str:
        """Represent as string."""
        return (
            f"<PluginStorage(scope={self.scope}, user_id={self.user_id}, "
            f"plugin={self.plugin_name}, ns={self.namespace}, key={self.key})>"
        )
