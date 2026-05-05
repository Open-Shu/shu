"""User models for Shu authentication system."""

from enum import Enum

from sqlalchemy import Boolean, Column, Index, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship

from ..models.base import BaseModel


class UserRole(Enum):
    """User roles for role-based access control."""

    ADMIN = "admin"  # Full system access, user management
    POWER_USER = "power_user"  # Access to multiple KBs, advanced features
    REGULAR_USER = "regular_user"  # Access to personal KB and assigned team KBs


class User(BaseModel):
    """User model with SSO integration via ProviderIdentity."""

    __tablename__ = "users"

    __table_args__ = (
        # Partial index for the SHU-507 verification lookup. The hash is
        # NULL in the steady state, so a full index is mostly empty rows;
        # the partial form keeps it small. Mirrors migration 008_0014.
        Index(
            "ix_users_email_verification_token_hash",
            "email_verification_token_hash",
            postgresql_where=text("email_verification_token_hash IS NOT NULL"),
        ),
    )

    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, default=UserRole.REGULAR_USER.value)  # Store as string
    # google_id column removed - use ProviderIdentity table instead
    picture_url = Column(String, nullable=True)
    is_active = Column(Boolean, default=False)  # Users require admin activation by default
    deactivation_scheduled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_login = Column(TIMESTAMP(timezone=True), nullable=True)

    # Password authentication fields
    password_hash = Column(String(255), nullable=True)  # Nullable for Google OAuth users
    auth_method = Column(String(50), nullable=False, default="google")  # 'google' or 'password'
    must_change_password = Column(Boolean, default=False, nullable=False, server_default="false")

    # Email verification (SHU-507) — separate from is_active. is_active is the
    # admin-controlled gate; email_verified is the user-controlled gate proving
    # ownership of the address. Login fails fast on is_active first, then on
    # email_verified (only when an email backend is configured).
    email_verified = Column(Boolean, default=False, nullable=False, server_default="false")
    # sha256 hex of the plaintext token. Plaintext only ever appears in the
    # verification email. NULL when no verification is pending.
    email_verification_token_hash = Column(String(64), nullable=True)
    email_verification_expires_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    preferences = relationship("UserPreferences", back_populates="user", uselist=False, cascade="all, delete-orphan")

    # RBAC relationships
    created_groups = relationship("UserGroup", foreign_keys="UserGroup.created_by", back_populates="creator")
    group_memberships = relationship(
        "UserGroupMembership",
        foreign_keys="UserGroupMembership.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    owned_knowledge_bases = relationship("KnowledgeBase", foreign_keys="KnowledgeBase.owner_id", back_populates="owner")

    # Provider relationships (OAuth identities and credentials)
    provider_identities = relationship(
        "ProviderIdentity", foreign_keys="ProviderIdentity.user_id", cascade="all, delete-orphan"
    )
    provider_credentials = relationship(
        "ProviderCredential",
        foreign_keys="ProviderCredential.user_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """Represent user as string."""
        return f"<User(email='{self.email}', role='{self.role}')>"

    @property
    def role_enum(self) -> UserRole:
        """Get role as enum."""
        return UserRole(self.role)

    def has_role(self, required_role: UserRole) -> bool:
        """Check if user has at least the required role level."""
        role_hierarchy = {UserRole.REGULAR_USER: 1, UserRole.POWER_USER: 2, UserRole.ADMIN: 3}

        user_level = role_hierarchy.get(self.role_enum, 0)
        required_level = role_hierarchy.get(required_role, 0)

        return user_level >= required_level

    def can_manage_users(self) -> bool:
        """Check if user can manage other users."""
        return self.role_enum == UserRole.ADMIN

    def can_access_admin_panel(self) -> bool:
        """Check if user can access admin panel."""
        return self.role_enum in [UserRole.ADMIN, UserRole.POWER_USER]

    def to_dict(self) -> dict:
        """Convert user to dictionary for API responses."""
        return {
            "user_id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "picture_url": self.picture_url,
            "is_active": self.is_active,
            "email_verified": self.email_verified,
            "auth_method": self.auth_method,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at.isoformat() if self.created_at is not None else None,
            "last_login": self.last_login.isoformat() if self.last_login is not None else None,
            "deactivation_scheduled_at": (
                self.deactivation_scheduled_at.isoformat() if self.deactivation_scheduled_at is not None else None
            ),
        }
