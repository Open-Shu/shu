"""Plugin Storage model for plugin runtime state.

Provides key/value storage for plugins scoped by
``(scope, user_id, plugin_name, namespace, key)``.

Separates plugin state (cursors, secrets, storage) from agent memory and
supports both per-user and system-wide entries via the ``scope`` column.
"""

from sqlalchemy import JSON, Column, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import BaseModel, TenantScopedMixin


class PluginStorage(TenantScopedMixin, BaseModel):
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

    # Tenant_id is included in both the UNIQUE constraint and the lookup
    # index because plugin_storage is tenant-scoped. Two tenants picking the
    # same (plugin_name, namespace, key) at scope='system' must not collide;
    # without tenant_id in the constraint, the second tenant's INSERT raises
    # a duplicate-key error even though RLS hides the other row from reads.
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scope",
            "user_id",
            "plugin_name",
            "namespace",
            "key",
            name="uq_plugin_storage_tenant_scope_key",
        ),
        Index(
            "ix_plugin_storage_lookup",
            "tenant_id",
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
