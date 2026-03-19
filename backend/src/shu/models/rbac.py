"""RBAC (Role-Based Access Control) Models.

This module contains the database models for group-based access control,
including user groups and memberships.
"""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..core.database import Base
from .base import BaseModel, UUIDMixin


class RBACBaseModel(Base, UUIDMixin):
    """Base model for RBAC tables that don't use standard timestamps."""

    __abstract__ = True

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}

    def __repr__(self) -> str:
        """Return string representation of the model."""
        return f"<{self.__class__.__name__}(id={self.id})>"


class GroupRole(str, Enum):
    """Roles within a user group."""

    MEMBER = "member"  # Regular group member
    ADMIN = "admin"  # Group administrator


class UserGroup(BaseModel):
    """User groups for team-based access control.

    Groups allow organizing users into teams (e.g., HR, Engineering, Marketing)
    and granting permissions to entire groups rather than individual users.
    """

    __tablename__ = "user_groups"

    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Audit fields
    created_by = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_groups")
    memberships = relationship("UserGroupMembership", back_populates="group", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<UserGroup(id='{self.id}', name='{self.name}', active={self.is_active})>"


class UserGroupMembership(RBACBaseModel):
    """User membership in groups with roles.

    This table tracks which users belong to which groups and their role
    within that group (member or admin).
    """

    __tablename__ = "user_group_memberships"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(String(36), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(50), default=GroupRole.MEMBER.value, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Audit fields
    granted_by = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    granted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="group_memberships")
    group = relationship("UserGroup", back_populates="memberships")
    granter = relationship("User", foreign_keys=[granted_by])

    # Constraints
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group_membership"),)

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<UserGroupMembership(user_id='{self.user_id}', group_id='{self.group_id}', role='{self.role}')>"
