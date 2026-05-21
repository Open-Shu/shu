"""SystemSetting model for storing configurable application values.

Provides a flexible key/value store where values are persisted as JSON blobs,
making it easy to add new application-wide settings without migrations.

Tenant-scoped (SHU-761): the PK is composite ``(tenant_id, key)`` so each
tenant has its own isolated namespace. Originally a single global key namespace,
but per-tenant callers like ``side_call_service`` (model-config selection) and
``branding_service`` (per-tenant branding) would collide across tenants on the
same key. RLS enforces the scope on every read/write.
"""

from sqlalchemy import Column, PrimaryKeyConstraint, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.types import JSON

from .base import Base, TenantScopedMixin, TimestampMixin


class SystemSetting(TenantScopedMixin, TimestampMixin, Base):
    """Tenant-scoped key/value settings table with JSON-backed values.

    The composite PK ``(tenant_id, key)`` is what makes "same key per tenant"
    work — without it, ``key`` alone would be globally unique and tenant A's
    write to ``side_call_model_config_id`` would clobber tenant B's.

    Tenant stamping on insert is handled by the ``before_flush`` listener in
    ``core/database.py``; no per-call boilerplate at write sites.
    """

    __tablename__ = "system_settings"

    key = Column(String(128), nullable=False, index=True)
    value = Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)

    __table_args__ = (PrimaryKeyConstraint("tenant_id", "key", name="system_settings_pkey"),)

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<SystemSetting(key='{self.key}')>"
