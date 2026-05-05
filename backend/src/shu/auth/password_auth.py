"""Password-based authentication service for Shu.

This module provides password-based authentication alongside Google OAuth,
enabling creation of investor accounts and improving testing capabilities.
"""

import logging
import secrets
import string
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
        self.special_chars: str = self.settings.password_special_chars
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

    def validate_password(self, password: str) -> list[str]:
        """Validate a password against the configured password policy.

        Checks the password against the rules defined by ``Settings.password_policy``:

        - ``moderate``: minimum N characters, at least one uppercase letter,
          one lowercase letter, and one digit.
        - ``strict``: all ``moderate`` rules plus at least one special character
          (``!@#$%^&*()-_+=``).

        Args:
            password: The plaintext password to validate.

        Returns:
            A list of human-readable error messages describing which rules were
            violated. An empty list means the password is valid.

        """
        errors: list[str] = []

        # Rules shared by both moderate and strict policies
        min_len = self.settings.password_min_length
        if len(password) < min_len:
            errors.append(f"Password must be at least {min_len} characters long")
        if not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")

        # Additional rule for strict policy
        if self.settings.password_policy == "strict" and not any(  # pragma: allowlist secret  # noqa: S105
            c in self.special_chars for c in password
        ):
            errors.append(f"Password must contain at least one special character ({self.special_chars})")

        return errors

    def generate_temporary_password(self, length: int = 16) -> str:
        """Generate a cryptographically secure temporary password.

        The generated password always satisfies the ``strict`` policy by
        guaranteeing at least one uppercase letter, one lowercase letter, one
        digit, and one special character. The remaining characters are chosen
        randomly from the full charset, and the final password is shuffled.

        Args:
            length: Desired password length. Must be at least 4 to satisfy all
                character-class requirements. Defaults to 16.

        Returns:
            A random password string of the requested length.

        """
        min_guaranteed = 4  # one uppercase, one lowercase, one digit, one special
        if length < min_guaranteed:
            raise ValueError(
                f"Password length must be at least {min_guaranteed} to satisfy all character-class requirements"
            )

        full_charset = string.ascii_letters + string.digits + self.special_chars

        # Guarantee at least one character from each required class
        guaranteed = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice(self.special_chars),
        ]

        # Fill remaining length with random characters from the full charset
        remaining = [secrets.choice(full_charset) for _ in range(length - len(guaranteed))]

        # Combine and shuffle to avoid predictable positions
        password_chars = guaranteed + remaining
        # Use Fisher-Yates shuffle via secrets for uniform distribution
        shuffled: list[str] = []
        pool = list(password_chars)
        while pool:
            idx = secrets.randbelow(len(pool))
            shuffled.append(pool.pop(idx))

        return "".join(shuffled)

    async def create_user(
        self,
        email: str,
        password: str,
        name: str,
        role: str = "regular_user",
        db: AsyncSession = None,
        admin_created: bool = False,
        flush_only: bool = False,
        requires_email_verification: bool = False,
    ) -> User:
        """Create a new user with password authentication.

        ``flush_only=True`` flushes the row (acquiring the unique-email
        constraint) without committing — used by the admin create path
        and by the SHU-507 verification path so issuing the verification
        token can ride the same transaction.

        ``requires_email_verification=True`` (SHU-507) creates the user
        active-but-unverified — `is_active=True` so admin activation is
        not needed, but `email_verified=False` blocks login until the
        user clicks the verification link. Only meaningful for self-
        registration; admin-created accounts are vouched-for and skip
        verification entirely.
        """
        # Check if user already exists
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise ValueError(f"User with email {email} already exists")

        # Decide is_active / email_verified per the SHU-507 state machine.
        # See the SHU-507 ticket "State machine" section.
        if admin_created:
            # Validate role for admin-created users
            try:
                UserRole(role)
            except ValueError:
                raise ValueError(f"Invalid role: {role}")
            is_active = True
            email_verified = True  # admin vouches for the email
        elif requires_email_verification:
            role = "regular_user"
            # SHU_AUTO_ACTIVATE_USERS controls whether email verification
            # alone is sufficient to log in. When False (default), the
            # admin activation gate is independently enforced — the user
            # must verify AND the admin must activate before login works.
            # When True, the operator has explicitly trusted email
            # verification as sufficient activation signal, and verifying
            # flips the only remaining gate.
            is_active = bool(self.settings.auto_activate_users)
            email_verified = False
        else:
            role = "regular_user"  # Force regular_user role for self-registration
            is_active = False  # Require admin activation (current legacy behaviour)
            email_verified = False

        # Hash password
        password_hash = self._hash_password(password)

        # Create user. last_login reflects actual logins, not creation —
        # leave NULL for verification-pending accounts (they cannot log in
        # yet). The pre-existing admin-created path keeps last_login=now()
        # to preserve the behaviour external admin reports may rely on.
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            auth_method="password",
            role=role,
            is_active=is_active,
            email_verified=email_verified,
            last_login=datetime.now(UTC) if (is_active and email_verified) else None,
        )

        db.add(user)
        if flush_only:
            await db.flush()
        else:
            await db.commit()
            await db.refresh(user)

        status = "active" if is_active else "inactive (requires admin activation)"
        if not email_verified and is_active:
            status = "active (pending email verification)"
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

        # Check if user is active (after validating credentials to avoid leaking account status)
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is inactive. Please contact an administrator for activation.",
            )

        # SHU-507: email verification gate. Only enforced when an email backend
        # is configured. Self-hosted deployments with email_backend=disabled
        # rely on the `is_active` admin gate above as the sole login gate;
        # email_verified stays False on those rows but is never checked here,
        # so admin activation alone is sufficient (legacy behaviour preserved).
        email_backend = (self.settings.email_backend or "disabled").strip().lower()
        if email_backend != "disabled" and not user.email_verified:
            logger.info(
                "Login blocked for %s (user %s): email not verified yet",
                user.email,
                user.id,
                extra={
                    "event": "login.blocked_unverified",
                    "user_id": user.id,
                    "email": user.email,
                },
            )
            # Distinct error message from the inactive case so the frontend
            # can offer a "resend verification" CTA. Same status code (400).
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please verify your email address. Check your inbox or use the resend link.",
            )

        # Update last login
        user.last_login = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        logger.info(f"Password authentication successful for user: {email}")
        return user

    async def _get_password_user(self, user_id: str, db: AsyncSession) -> User:
        """Look up a user by ID and verify they use password authentication.

        Args:
            user_id: The ID of the user to look up.
            db: The async database session.

        Returns:
            The ``User`` instance.

        Raises:
            LookupError: If the user is not found.
            ValueError: If the user does not use password authentication.

        """
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise LookupError("User not found")

        if user.auth_method != "password":
            raise ValueError("This user account does not use password authentication")

        return user

    async def _apply_new_password(
        self,
        user: User,
        new_password: str,
        db: AsyncSession,
        *,
        must_change: bool,
    ) -> None:
        """Validate, hash, and store a new password for the given user.

        Args:
            user: The user whose password is being updated.
            new_password: The plaintext new password.
            db: The async database session.
            must_change: Value to set on ``user.must_change_password``.

        Raises:
            ValueError: If the password fails policy validation.

        """
        validation_errors = self.validate_password(new_password)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        user.password_hash = self._hash_password(new_password)
        user.must_change_password = must_change
        await db.commit()

    async def change_password(self, user_id: str, old_password: str, new_password: str, db: AsyncSession) -> bool:
        """Change a user's password."""
        user = await self._get_password_user(user_id, db)

        # Verify current password
        if not user.password_hash or not self._verify_password(old_password, user.password_hash):
            raise ValueError("Current password is incorrect")

        # Ensure new password differs from current password
        if self._verify_password(new_password, user.password_hash):
            raise ValueError("New password must be different from current password")

        await self._apply_new_password(user, new_password, db, must_change=False)

        logger.info(f"Password changed for user: {user.email}")
        return True

    async def reset_password(self, user_id: str, db: AsyncSession, new_password: str | None = None) -> str:
        """Reset a user's password (admin function).

        Generates a temporary password if none is provided, validates it against
        the active password policy, hashes and stores it, and sets the
        ``must_change_password`` flag so the user is forced to choose a new
        password on next login.

        Args:
            user_id: The ID of the user whose password should be reset.
            db: The async database session.
            new_password: Optional plaintext password. If ``None``, a
                cryptographically secure temporary password is generated.

        Returns:
            The plaintext password (generated or provided) so the caller can
            relay it to the admin.

        Raises:
            LookupError: If the user is not found.
            ValueError: If the user does not use password authentication
                or the password fails policy validation.

        """
        user = await self._get_password_user(user_id, db)

        if new_password is None:
            length = max(16, self.settings.password_min_length)
            new_password = self.generate_temporary_password(length=length)

        await self._apply_new_password(user, new_password, db, must_change=True)

        logger.info(f"Password reset for user: {user.email}")
        return new_password


# Global instance
password_auth_service = PasswordAuthService()
