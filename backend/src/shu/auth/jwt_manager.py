"""JWT token management for Shu authentication."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)


def is_token_revoked_by_password_change(
    token_iat: int | None,
    password_changed_at: datetime | None,
) -> bool:
    """Return True if a token's `iat` predates the user's most recent
    password change (SHU-745).

    Both sides are floored to integer seconds before comparison: JWT `iat`
    is serialised as integer seconds by the encoder, while
    `password_changed_at` is microsecond-precision. Without flooring, a
    token issued in the same wall-clock second as the reset would compare
    as strictly less and produce a false-positive 401 — the user's freshly
    issued token would bounce on its first request.

    Returns False when either argument is None — `None` for either side
    means "no invalidation gate applies" (existing accounts that never
    reset, or token without an iat claim).
    """
    if token_iat is None or password_changed_at is None:
        return False
    if password_changed_at.tzinfo is None:
        password_changed_at = password_changed_at.replace(tzinfo=UTC)
    return int(token_iat) < int(password_changed_at.timestamp())


class JWTManager:
    """JWT token management for Shu authentication."""

    def __init__(self) -> None:
        settings = get_settings_instance()
        self.secret_key = settings.jwt_secret_key
        self.algorithm = "HS256"
        self.access_token_expire_minutes = settings.jwt_access_token_expire_minutes
        self.refresh_token_expire_days = settings.jwt_refresh_token_expire_days

        if not self.secret_key:
            raise ValueError("JWT_SECRET_KEY not configured in settings")

    def create_access_token(self, user_data: dict[str, Any]) -> str:
        """Create JWT access token with user information."""
        if not self.secret_key:
            raise ValueError("JWT secret key not configured")

        expire = datetime.now(UTC) + timedelta(minutes=self.access_token_expire_minutes)

        payload = {
            "user_id": user_data["user_id"],
            "email": user_data["email"],
            "role": user_data["role"],
            "exp": expire,
            "iat": datetime.now(UTC),
            "type": "access",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, user_id: str) -> str:
        """Create JWT refresh token."""
        if not self.secret_key:
            raise ValueError("JWT secret key not configured")

        expire = datetime.now(UTC) + timedelta(days=self.refresh_token_expire_days)

        payload = {"user_id": user_id, "exp": expire, "iat": datetime.now(UTC), "type": "refresh"}

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify and decode JWT token."""
        if not self.secret_key:
            logger.error("JWT secret key not configured")
            return None

        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except JWTError as e:
            logger.warning(f"JWT verification failed: {e}")
            return None

    def extract_user_from_token(self, token: str) -> dict[str, Any] | None:
        """Extract user information from access token."""
        payload = self.verify_token(token)
        if not payload or payload.get("type") != "access":
            return None

        return {
            "user_id": payload.get("user_id"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            # `iat` (epoch seconds) drives SHU-745 session invalidation —
            # the JWT auth middleware rejects tokens whose `iat` is older
            # than the user's `password_changed_at`.
            "iat": payload.get("iat"),
        }

    def is_token_expired(self, token: str) -> bool:
        """Check if token is expired without raising exception."""
        if not self.secret_key:
            return True

        try:
            jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return False
        except JWTError:
            return True

    def is_token_near_expiry(self, token: str, buffer_minutes: int = 5) -> bool:
        """Check if token will expire within buffer_minutes."""
        if not self.secret_key:
            return True

        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            exp_timestamp = payload.get("exp")
            if not exp_timestamp:
                return True

            exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=UTC)
            buffer_time = datetime.now(UTC) + timedelta(minutes=buffer_minutes)

            return buffer_time >= exp_datetime
        except JWTError:
            return True

    def refresh_access_token(self, refresh_token: str) -> str | None:
        """Create new access token from valid refresh token."""
        if not self.secret_key:
            logger.error("JWT secret key not configured")
            return None

        try:
            # Verify refresh token
            payload = jwt.decode(refresh_token, self.secret_key, algorithms=[self.algorithm])

            # Check if it's a refresh token
            if payload.get("type") != "refresh":
                logger.warning("Token is not a refresh token")
                return None

            user_id = payload.get("user_id")
            if not user_id:
                logger.warning("No user_id in refresh token")
                return None

            # For refresh, we need to get current user data from database
            # This will be handled by the endpoint that calls this method
            return user_id

        except JWTError as e:
            logger.warning(f"Refresh token verification failed: {e}")
            return None
