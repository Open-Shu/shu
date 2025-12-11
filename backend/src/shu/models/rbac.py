"""
RBAC (Role-Based Access Control) Models

This module contains the database models for granular knowledge base
access control, including user groups and permissions.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, String, Text,
    UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship

from .base import BaseModel, UUIDMixin
from ..core.database import Base


class RBACBaseModel(Base, UUIDMixin):
    """Base model for RBAC tables that don't use standard timestamps."""

    __abstract__ = True

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }

    def __repr__(self) -> str:
        """Return string representation of the model."""
        return f"<{self.__class__.__name__}(id={self.id})>"


class PermissionLevel(str, Enum):
    """Permission levels for knowledge base access."""
    OWNER = "owner"          # Full control, can delete KB, manage permissions
    ADMIN = "admin"          # Can modify KB, add/remove documents, manage members
    MEMBER = "member"        # Can query KB, view documents, add documents
    READ_ONLY = "read_only"  # Can only query KB, no modifications


class GroupRole(str, Enum):
    """Roles within a user group."""
    MEMBER = "member"  # Regular group member
    ADMIN = "admin"    # Group administrator


class UserGroup(BaseModel):
    """
    User groups for team-based access control.
    
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
    permissions = relationship("KnowledgeBasePermission", back_populates="group", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<UserGroup(id='{self.id}', name='{self.name}', active={self.is_active})>"


class UserGroupMembership(RBACBaseModel):
    """
    User membership in groups with roles.
    
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
    granted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="group_memberships")
    group = relationship("UserGroup", back_populates="memberships")
    granter = relationship("User", foreign_keys=[granted_by])

    # Constraints
    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_user_group_membership'),
    )

    def __repr__(self):
        return f"<UserGroupMembership(user_id='{self.user_id}', group_id='{self.group_id}', role='{self.role}')>"


class KnowledgeBasePermission(RBACBaseModel):
    """
    Granular permissions for knowledge base access.
    
    This table defines who (users or groups) can access which knowledge bases
    and at what permission level. Each permission can be granted to either
    a specific user OR a group, but not both.
    """
    __tablename__ = "knowledge_base_permissions"

    knowledge_base_id = Column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)

    # Either user_id OR group_id must be set, but not both
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id = Column(String(36), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=True, index=True)

    permission_level = Column(String(50), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Optional expiration for temporary access
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Audit fields
    granted_by = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    granted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="permissions")
    user = relationship("User", foreign_keys=[user_id], back_populates="kb_permissions")
    group = relationship("UserGroup", back_populates="permissions")
    granter = relationship("User", foreign_keys=[granted_by])

    # Constraints
    __table_args__ = (
        # Ensure either user_id OR group_id is set, but not both
        CheckConstraint(
            '(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)',
            name='chk_permission_target'
        ),
        # Prevent duplicate permissions
        UniqueConstraint('knowledge_base_id', 'user_id', 'group_id', name='uq_kb_permission'),
    )

    @property
    def target_type(self) -> str:
        """Return whether this permission is for a user or group."""
        return "user" if self.user_id else "group"

    @property
    def target_id(self) -> str:
        """Return the ID of the permission target (user or group)."""
        return self.user_id or self.group_id

    def is_expired(self) -> bool:
        """Check if this permission has expired."""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def __repr__(self):
        target = f"user:{self.user_id}" if self.user_id else f"group:{self.group_id}"
        return f"<KnowledgeBasePermission(kb='{self.knowledge_base_id}', target='{target}', level='{self.permission_level}')>"
