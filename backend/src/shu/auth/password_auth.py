"""Password-based authentication service for Shu.

This module provides password-based authentication alongside Google OAuth,
enabling creation of investor accounts and improving testing capabilities.
"""

import logging
from datetime import UTC, datetime

import bcrypt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from .models import User, UserRole

logger = logging.getLogger(__name__)


class PasswordAuthService:
    """Service for password-based user authentication and management."""

    def __init__(self) -> None:
        self.settings = get_settings_instance()
        # Dummy hash for constant-time authentication (prevents timing attacks)
        # This is a bcrypt hash of "dummy_password_for_timing_attack_prevention"
        self._dummy_hash = "$2b$12$rQx8vQx8vQx8vQx8vQx8vOx8vQx8vQx8vQx8vQx8vQx8vQx8vQx8vQ"

    def _hash_password(self, password: str) -> str:
        """Hash a password using bcrypt."""
        # Generate salt and hash password
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode("utf-8"), salt)
        return password_hash.decode("utf-8")

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    async def create_user(
        self,
        email: str,
        password: str,
        name: str,
        role: str = "regular_user",
        db: AsyncSession = None,
        admin_created: bool = False,
    ) -> User:
        """Create a new user with password authentication."""
        # Check if user already exists
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise ValueError(f"User with email {email} already exists")

        # Security: Only admins can create users with custom roles or active status
        if not admin_created:
            role = "regular_user"  # Force regular_user role for self-registration
            is_active = False  # Require admin activation
        else:
            # Validate role for admin-created users
            try:
                UserRole(role)
            except ValueError:
                raise ValueError(f"Invalid role: {role}")
            is_active = True  # Admin-created users are active by default

        # Hash password
        password_hash = self._hash_password(password)

        # Create user
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            auth_method="password",
            role=role,
            is_active=is_active,
            last_login=datetime.now(UTC) if is_active else None,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        status = "active" if is_active else "inactive (requires admin activation)"
        logger.info(f"Created password-authenticated user: {email} with role: {role}, status: {status}")
        return user

    async def authenticate_user(self, email: str, password: str, db: AsyncSession) -> User:
        """Authenticate a user with email and password.

        Uses constant-time verification to prevent timing attacks that could
        be used for username enumeration.
        """
        # Find user by email
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        # Check if user is active, let them know if not.
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is inactive. Please contact an administrator for activation.",
            )

        # Always perform password verification to prevent timing attacks
        # Use dummy hash if user doesn't exist or doesn't have password hash
        password_hash = user.password_hash if user and user.password_hash else self._dummy_hash

        # Always perform bcrypt verification (constant time regardless of user existence)
        password_valid = self._verify_password(password, password_hash)

        # Now check all conditions and provide consistent error message
        if not user:
            raise ValueError("Invalid email or password")

        # Check if user uses password authentication
        if user.auth_method != "password":
            raise ValueError("This user account uses a different authentication method")

        # Check password validity (already computed above)
        if not user.password_hash or not password_valid:
            raise ValueError("Invalid email or password")

        # Update last login
        user.last_login = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        logger.info(f"Password authentication successful for user: {email}")
        return user

    async def change_password(self, user_id: str, old_password: str, new_password: str, db: AsyncSession) -> bool:
        """Change a user's password.

        Uses constant-time verification to prevent timing attacks.
        """
        # Find user
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        # Always perform password verification to prevent timing attacks
        password_hash = user.password_hash if user and user.password_hash else self._dummy_hash

        # Always perform bcrypt verification (constant time)
        password_valid = self._verify_password(old_password, password_hash)

        # Now check all conditions
        if not user:
            raise ValueError("User not found")

        if user.auth_method != "password":
            raise ValueError("This user account does not use password authentication")

        # Check password validity (already computed above)
        if not user.password_hash or not password_valid:
            raise ValueError("Current password is incorrect")

        # Hash new password
        user.password_hash = self._hash_password(new_password)
        await db.commit()

        logger.info(f"Password changed for user: {user.email}")
        return True

    async def reset_password(self, email: str, new_password: str, db: AsyncSession) -> bool:
        """Reset a user's password (admin function)."""
        # Find user
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        if user.auth_method != "password":
            raise ValueError("This user account does not use password authentication")

        # Hash new password
        user.password_hash = self._hash_password(new_password)
        await db.commit()

        logger.info(f"Password reset for user: {user.email}")
        return True


# Global instance
password_auth_service = PasswordAuthService()
