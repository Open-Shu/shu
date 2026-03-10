"""Access Policy Models for Policy-Based Access Control (PBAC).

This module contains the database models for the policy-based access control
engine, including policies, actor bindings, and resource/action statements.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import relationship

from .base import BaseModel


class AccessPolicy(BaseModel):
    """Access control policy with allow/deny effect.

    Policies define named, reusable access rules that bind actors (users/groups)
    to actions on resources with allow/deny semantics. Inactive policies
    (is_active=False) are excluded from all access evaluations.
    """

    __tablename__ = "access_policies"

    __table_args__ = (CheckConstraint("effect IN ('allow', 'deny')", name="chk_policy_effect"),)

    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    effect = Column(String(10), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Audit fields
    created_by = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Relationships
    creator = relationship("User", foreign_keys=[created_by])
    bindings = relationship("AccessPolicyBinding", back_populates="policy", cascade="all, delete-orphan")
    statements = relationship("AccessPolicyStatement", back_populates="policy", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<AccessPolicy(id='{self.id}', name='{self.name}', effect='{self.effect}', active={self.is_active})>"


class AccessPolicyBinding(BaseModel):
    """Binding that associates an actor (user or group) with a policy.

    Each binding links a policy to a specific actor by type and ID.
    The unique constraint on (policy_id, actor_type, actor_id) prevents
    duplicate bindings for the same actor on the same policy.
    """

    __tablename__ = "access_policy_bindings"

    policy_id = Column(
        String(36),
        ForeignKey("access_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_type = Column(String(10), nullable=False, index=True)  # "user" or "group"
    actor_id = Column(String(36), nullable=False, index=True)

    # Relationships
    policy = relationship("AccessPolicy", back_populates="bindings")

    __table_args__ = (
        UniqueConstraint("policy_id", "actor_type", "actor_id", name="uq_binding_policy_actor"),
        CheckConstraint("actor_type IN ('user', 'group')", name="chk_binding_actor_type"),
    )

    def __repr__(self) -> str:
        """Represent as string."""
        return (
            f"<AccessPolicyBinding(policy_id='{self.policy_id}', "
            f"actor_type='{self.actor_type}', actor_id='{self.actor_id}')>"
        )


class AccessPolicyStatement(BaseModel):
    """Statement defining actions and resources for a policy.

    Each statement contains a list of actions (e.g., 'experience.read',
    'plugin.execute') and resources (e.g., 'experience:*', 'plugin:shu_gmail_*')
    stored as JSON arrays.
    """

    __tablename__ = "access_policy_statements"

    policy_id = Column(
        String(36),
        ForeignKey("access_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actions = Column(JSON, nullable=False)  # ["experience.read", "plugin.execute"]
    resources = Column(JSON, nullable=False)  # ["experience:*", "plugin:shu_gmail_*"]

    # Relationships
    policy = relationship("AccessPolicy", back_populates="statements")

    def __repr__(self) -> str:
        """Represent as string."""
        return (
            f"<AccessPolicyStatement(policy_id='{self.policy_id}', "
            f"actions={self.actions}, resources={self.resources})>"
        )
