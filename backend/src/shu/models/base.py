"""Base model class for Shu RAG Backend.

This module provides the base model class with common functionality
for all database models.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column

# Import Base from the database module to avoid duplicate declarations
from ..core.database import Base


@declarative_mixin
class TimestampMixin:
    """Mixin for adding timestamp columns to models."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


@declarative_mixin
class UUIDMixin:
    """Mixin for adding UUID primary key to models."""

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))


@declarative_mixin
class TenantScopedMixin:
    """Mixin that marks a model as tenant-scoped and stamps its rows with a tenant id.

    Presence of this mixin is the inventory signal used by the RLS enforcement tests
    and by the auto-stamping ``before_flush`` listener; opting in is per-model since
    some catalogs (tiers, plugin manifests, etc.) are intentionally global.
    """

    # ondelete=RESTRICT: tenant deletion must be a deliberate, explicit operation —
    # an accidental cascade would silently destroy customer data across every table.
    # index=True: every query under RLS filters on tenant_id, so the index is what
    # keeps that mandatory predicate cheap.
    tenant_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class BaseModel(Base, TimestampMixin, UUIDMixin):
    """Base model class with common functionality."""

    __abstract__ = True

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        # Use ORM mapper attributes to respect attribute keys when column names differ
        result = {}
        for attr in self.__mapper__.column_attrs:  # type: ignore[attr-defined] # SQLAlchemy adds __mapper__
            try:
                result[attr.key] = getattr(self, attr.key)
            except MissingGreenlet:
                # If a deferred field triggers MissingGreenlet during serialization, return None for that field
                result[attr.key] = None
        return result

    def __repr__(self) -> str:
        """Return string representation of the model."""
        return f"<{self.__class__.__name__}(id={self.id})>"
