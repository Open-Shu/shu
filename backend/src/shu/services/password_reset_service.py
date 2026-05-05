"""Password reset flow for password-authenticated users (SHU-745).

Self-service email-based password reset, built on the SHU-508 EmailService
mid-layer. Users request a reset by email address, receive a tokenised
link, set a new password, and have all existing sessions invalidated as
a side effect of the reset.

State transitions:

* `request_reset` → rate-limit (per-IP + per-email) → silent no-op for
  unknown / SSO-only / inactive addresses (no enumeration) → for known
  password users, issue a fresh `password_reset_token` row and queue
  the reset email
* `complete_reset` → validate token (exists, not expired, not used,
  matching user) → apply password policy → update password → mark
  token used → mark all *other* outstanding tokens for the user used →
  bump `password_changed_at` (invalidates all existing JWTs)
* `resend_from_token` → token-as-identity recovery: the verify page
  hands the (possibly expired) token back, server resolves the user
  from the hash and issues a fresh token. The user never has to
  retype or even see their email address.

Why a separate service file (vs adding to `password_auth.py`):
verification (SHU-507) and reset (SHU-745) follow parallel patterns
and the `password_auth.py` file already does heavy lifting for create /
authenticate / change-password. Separating the reset flow keeps each
file's responsibility crisp and the diff between SHU-507 and SHU-745
mirror-images visible.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.cache_backend import CacheBackend
from ..core.logging import get_logger
from ..models.password_reset_token import PasswordResetToken
from .email_service import EmailService

logger = get_logger(__name__)


# Rate-limit policy. Numbers live here (the policy layer) rather than
# in EmailService (the helper layer).
#
# Per-recipient bucket: 3 requests per hour per email address — same
# bucket SHU-507 verification uses, so a user spamming "send me a link"
# across multiple endpoints sees one consistent budget.
_REQUEST_PER_EMAIL_MAX = 3
_REQUEST_PER_EMAIL_WINDOW_SECONDS = 3600

# Per-IP bucket: 10 requests per hour from a single source. Higher than
# the per-email bucket so a small office NAT'd behind one IP can still
# issue resets for a few users in a short window.
_REQUEST_PER_IP_MAX = 10
_REQUEST_PER_IP_WINDOW_SECONDS = 3600

# Reset endpoint: per-token-prefix rate limit to slow online brute force.
# 32 url-safe bytes is ~256 bits of entropy so a brute-force is already
# infeasible, but the cap is cheap insurance.
_RESET_PER_TOKEN_PREFIX_MAX = 5
_RESET_PER_TOKEN_PREFIX_WINDOW_SECONDS = 60


class PasswordResetError(Exception):
    """Base error raised by the reset service for caller-visible problems."""


class TokenInvalidError(PasswordResetError):
    """Raised when the supplied reset token does not match a row, has
    been consumed, or otherwise cannot complete the reset. Callers map
    this to a 400 with a generic detail string.
    """


class TokenExpiredError(TokenInvalidError):
    """Raised when the supplied token matches a real row but has expired.

    Subclasses ``TokenInvalidError`` so existing ``except TokenInvalidError``
    blocks continue to catch it. The endpoint surfaces this with a
    structured ``code`` (``PASSWORD_RESET_TOKEN_EXPIRED``) so the frontend
    can switch to the token-based "send a new reset link" UX without
    asking the user to retype their email.
    """


class PasswordPolicyError(PasswordResetError):
    """Raised when the supplied new password does not satisfy the
    configured policy. Caller maps to 400 with the validation errors as
    the detail string.
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class RateLimitedError(PasswordResetError):
    """Raised when the reset endpoint hits its per-token-prefix rate cap."""


def _hash_token(plaintext: str) -> str:
    """sha256 hex of a reset token. 64 chars."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class PasswordResetService:
    """Owns issuing, completing, and resending password reset tokens."""

    def __init__(
        self,
        *,
        email_service: EmailService,
        cache: CacheBackend,
        password_validator,
        password_hasher,
        token_ttl_seconds: int,
        app_base_url: str,
    ) -> None:
        self._email = email_service
        self._cache = cache
        self._validate_password = password_validator
        self._hash_password = password_hasher
        self._token_ttl = token_ttl_seconds
        self._app_base_url = app_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_reset(self, email: str, ip: str | None, db: AsyncSession) -> None:
        """Issue a reset token for `email` if it belongs to a known active
        password user.

        Always returns None. The endpoint cannot distinguish between any of
        these cases without leaking enumeration:

        * unknown email
        * SSO-only user (no password to reset)
        * inactive user
        * rate-limited request

        All of them are silent no-ops. The only observable distinction is
        whether an email is delivered — and the attacker has no in-band
        way to detect that.
        """
        # Per-IP rate-limit FIRST so unknown addresses cannot be probed at
        # unlimited speed for timing differences. The per-email bucket is
        # checked second; both must pass for a request to proceed.
        if ip and not await self._consume_ip_bucket(ip):
            logger.info(
                "Password reset request rate-limited (per-IP cap of %d/%ds hit for %s)",
                _REQUEST_PER_IP_MAX,
                _REQUEST_PER_IP_WINDOW_SECONDS,
                ip,
                extra={"event": "password_reset.request_rate_limited_ip"},
            )
            return

        allowed = await self._email.check_rate_limit(
            template_name="password_reset",
            to=email,
            max_per_window=_REQUEST_PER_EMAIL_MAX,
            window_seconds=_REQUEST_PER_EMAIL_WINDOW_SECONDS,
        )
        if not allowed:
            # Don't log the email at WARNING — log feeds are an
            # enumeration surface if an attacker has read access.
            logger.info(
                "Password reset request rate-limited (per-recipient cap of %d/%ds hit)",
                _REQUEST_PER_EMAIL_MAX,
                _REQUEST_PER_EMAIL_WINDOW_SECONDS,
                extra={"event": "password_reset.request_rate_limited_email"},
            )
            return

        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            logger.info(
                "Password reset requested for unknown address %s — no-op (no enumeration)",
                email,
                extra={"event": "password_reset.requested_unknown", "email": email},
            )
            return

        if user.auth_method != "password" or not user.password_hash:
            # SSO-only users have no password to reset. Silent no-op so
            # an attacker cannot use this endpoint to discover whether
            # an address is registered as SSO vs password.
            logger.info(
                "Password reset requested for %s (user %s) but the account uses %s auth — no-op",
                email,
                user.id,
                user.auth_method,
                extra={
                    "event": "password_reset.requested_wrong_auth_method",
                    "email": email,
                    "user_id": user.id,
                    "auth_method": user.auth_method,
                },
            )
            return

        if not user.is_active:
            # Deactivated accounts cannot reset their way back in. Silent
            # no-op — admin would have to re-activate.
            logger.info(
                "Password reset requested for %s (user %s) but account is inactive — no-op",
                email,
                user.id,
                extra={
                    "event": "password_reset.requested_inactive",
                    "email": email,
                    "user_id": user.id,
                },
            )
            return

        # Invalidate every prior outstanding token for this user before
        # issuing a new one. The ticket explicitly calls this out: "the
        # password_reset_token table needs a *history* of issued tokens
        # to invalidate older ones when a newer one is requested." The
        # net effect is a "newest wins" rule that bounds the live token
        # surface to one per user. Each invalidated row gets an audit
        # log entry so ops can correlate "user reports the first link
        # stopped working" with "they requested a second one."
        invalidated_count = await self._invalidate_outstanding(user.id, db)
        if invalidated_count:
            logger.info(
                "Invalidated %d prior outstanding password reset token(s) for %s (user %s)",
                invalidated_count,
                user.email,
                user.id,
                extra={
                    "event": "password_reset.token_invalidated_by_newer",
                    "user_id": user.id,
                    "email": user.email,
                    "count": invalidated_count,
                },
            )

        plaintext = secrets.token_urlsafe(32)
        token_row = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(plaintext),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._token_ttl),
            created_ip=ip,
            created_at=datetime.now(UTC),
        )
        db.add(token_row)
        await db.flush()

        reset_url = self._build_reset_url(plaintext)
        await self._email.send(
            db=db,
            template_name="password_reset",
            to=user.email,
            context={
                "name": user.name,
                "reset_url": reset_url,
                "expires_in_hours": max(1, self._token_ttl // 3600),
            },
            idempotency_key=f"password_reset:{user.id}:{token_row.token_hash}",
        )

        logger.info(
            "Password reset issued for %s (user %s); expires in %ds",
            user.email,
            user.id,
            self._token_ttl,
            extra={
                "event": "password_reset.requested",
                "user_id": user.id,
                "email": user.email,
                "ttl_seconds": self._token_ttl,
                "ip": ip,
            },
        )

    async def complete_reset(self, token: str, new_password: str, db: AsyncSession) -> User:
        """Apply a new password using a valid reset token.

        Validates the token, runs the password-policy validator, hashes
        and stores the new password, marks the token used, marks all
        other outstanding tokens for the user used (so a stockpile of
        valid tokens cannot keep working after one is consumed), bumps
        ``password_changed_at`` to invalidate every existing JWT for
        this user, and clears ``must_change_password`` if set.

        Returns the User row on success.

        Raises:
        * ``RateLimitedError`` — per-token-prefix cap hit
        * ``TokenExpiredError`` — token matched a row but expired
        * ``TokenInvalidError`` — token unknown, already used, or empty
        * ``PasswordPolicyError`` — new_password fails policy validation

        """
        if not token:
            raise TokenInvalidError("reset token is required")

        # Per-token-prefix rate-limit. The 32-byte token has too much
        # entropy to brute force, but a small cap is cheap insurance.
        # Bucket on the first 8 chars so multiple tokens issued for the
        # same user (e.g. successive requests during testing) don't
        # share a bucket.
        prefix = token[:8]
        bucket_key = f"password_reset_attempts:{prefix}"
        count = await self._cache.incr(bucket_key)
        if count == 1:
            await self._cache.expire(bucket_key, _RESET_PER_TOKEN_PREFIX_WINDOW_SECONDS)
        if count > _RESET_PER_TOKEN_PREFIX_MAX:
            logger.info(
                "Password reset attempts rate-limited (prefix bucket %s)",
                prefix,
                extra={"event": "password_reset.attempts_rate_limited"},
            )
            raise RateLimitedError("too many attempts; try again in a minute")

        token_hash = _hash_token(token)
        stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        result = await db.execute(stmt)
        token_row = result.scalar_one_or_none()

        if token_row is None:
            logger.info(
                "Password reset failed: unknown token (hash matched no row)",
                extra={"event": "password_reset.attempt_failed", "reason": "unknown_token"},
            )
            raise TokenInvalidError("reset token is invalid")

        if token_row.used_at is not None:
            logger.info(
                "Password reset failed: token already used at %s (user %s)",
                token_row.used_at.isoformat(),
                token_row.user_id,
                extra={
                    "event": "password_reset.attempt_failed",
                    "reason": "already_used",
                    "user_id": token_row.user_id,
                },
            )
            raise TokenInvalidError("reset token is invalid")

        # SQLite-via-aiosqlite (used in pytest E2E tests) strips tz info
        # from TIMESTAMP(timezone=True) columns. Normalize to UTC-aware
        # so the comparison behaves consistently across drivers.
        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires_at:
            logger.info(
                "Password reset failed: token expired at %s (user %s)",
                expires_at.isoformat(),
                token_row.user_id,
                extra={
                    "event": "password_reset.attempt_failed",
                    "reason": "expired",
                    "user_id": token_row.user_id,
                    "expired_at": expires_at.isoformat(),
                },
            )
            raise TokenExpiredError("reset token has expired")

        # Resolve the user. If the row was orphaned (FK cascade should
        # prevent this, but defensive) treat as invalid.
        user_stmt = select(User).where(User.id == token_row.user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        if user is None or user.auth_method != "password":
            logger.warning(
                "Password reset attempted but user (id=%s) is missing or non-password — investigate",
                token_row.user_id,
                extra={
                    "event": "password_reset.attempt_failed",
                    "reason": "user_missing_or_sso",
                    "user_id": token_row.user_id,
                },
            )
            raise TokenInvalidError("reset token is invalid")

        # Policy. Reuses the same validator the registration path uses,
        # so policy lives in one place.
        validation_errors = self._validate_password(new_password)
        if validation_errors:
            raise PasswordPolicyError(validation_errors)

        now = datetime.now(UTC)
        # Setting password_hash via the ORM fires the @validates hook on
        # User which bumps password_changed_at — see auth/models.py. We
        # don't set it explicitly here because the validator is the single
        # source of truth for that bump, applied uniformly across every
        # password-mutation path.
        user.password_hash = self._hash_password(new_password)
        user.must_change_password = False
        token_row.used_at = now

        # Invalidate every *other* outstanding token for this user — a
        # stockpile of valid tokens cannot keep working after one has
        # been consumed. Same `used_at=now` mark request-time
        # invalidation uses, so the sweep doesn't have to distinguish
        # "consumed by reset" from "superseded by a newer reset."
        await self._invalidate_outstanding(user.id, db, exclude_id=token_row.id, when=now)
        await db.flush()

        logger.info(
            "Password reset completed for %s (user %s); sessions invalidated",
            user.email,
            user.id,
            extra={
                "event": "password_reset.completed",
                "user_id": user.id,
                "email": user.email,
            },
        )
        return user

    async def resend_from_token(self, token: str, db: AsyncSession) -> None:
        """Re-issue a reset token using a (possibly expired) old token as
        the identity proof.

        The reset page knows the token from the URL even when it has
        expired. Handing the token back here lets the server hash it,
        resolve the user, and issue a fresh token without the user
        having to retype their email.

        Always returns None. Silent no-op on every failure branch (no
        enumeration). The leak surface is zero because the only way to
        trigger this flow is to already possess a token for the target
        user — issuing a fresh email to the legitimate owner is benign.
        """
        if not token:
            return

        token_hash = _hash_token(token)
        stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        result = await db.execute(stmt)
        token_row = result.scalar_one_or_none()
        if token_row is None:
            logger.info(
                "Token-based reset resend requested for an unknown token hash — no-op",
                extra={"event": "password_reset.resend_from_token_unknown"},
            )
            return

        user_stmt = select(User).where(User.id == token_row.user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        if user is None or user.auth_method != "password" or not user.is_active:
            logger.info(
                "Token-based reset resend ineligible (user_id=%s) — no-op",
                token_row.user_id,
                extra={
                    "event": "password_reset.resend_from_token_ineligible",
                    "user_id": token_row.user_id,
                },
            )
            return

        # Per-recipient rate limit (same bucket the email-based request
        # endpoint uses). Consistency: the per-user cap holds regardless
        # of which surface initiated the resend.
        allowed = await self._email.check_rate_limit(
            template_name="password_reset",
            to=user.email,
            max_per_window=_REQUEST_PER_EMAIL_MAX,
            window_seconds=_REQUEST_PER_EMAIL_WINDOW_SECONDS,
        )
        if not allowed:
            logger.info(
                "Token-based reset resend rate-limited for %s",
                user.email,
                extra={
                    "event": "password_reset.resend_from_token_rate_limited",
                    "email": user.email,
                    "user_id": user.id,
                },
            )
            return

        # Same "newest wins" rule as request_reset — token-based resend
        # is also a fresh issue, so the older token (and any siblings)
        # must be invalidated before we mint a new one.
        invalidated_count = await self._invalidate_outstanding(user.id, db)
        if invalidated_count:
            logger.info(
                "Invalidated %d prior outstanding password reset token(s) for %s (token-based resend)",
                invalidated_count,
                user.email,
                extra={
                    "event": "password_reset.token_invalidated_by_newer",
                    "user_id": user.id,
                    "email": user.email,
                    "count": invalidated_count,
                },
            )

        plaintext = secrets.token_urlsafe(32)
        new_row = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(plaintext),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._token_ttl),
            # The IP that originally requested is the one we have most
            # recent context for; the resend doesn't carry a fresh IP
            # because the verify-page surface is the user's browser hitting
            # the same server. Leave NULL.
            created_ip=None,
            created_at=datetime.now(UTC),
        )
        db.add(new_row)
        await db.flush()

        reset_url = self._build_reset_url(plaintext)
        await self._email.send(
            db=db,
            template_name="password_reset",
            to=user.email,
            context={
                "name": user.name,
                "reset_url": reset_url,
                "expires_in_hours": max(1, self._token_ttl // 3600),
            },
            idempotency_key=f"password_reset:{user.id}:{new_row.token_hash}",
        )

        logger.info(
            "Token-based reset resend issued for %s (user %s)",
            user.email,
            user.id,
            extra={
                "event": "password_reset.resend_from_token_issued",
                "user_id": user.id,
                "email": user.email,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _consume_ip_bucket(self, ip: str) -> bool:
        """Return True when the per-IP request bucket still has slots."""
        key = f"password_reset_ip:{ip}"
        count = await self._cache.incr(key)
        if count == 1:
            await self._cache.expire(key, _REQUEST_PER_IP_WINDOW_SECONDS)
        return count <= _REQUEST_PER_IP_MAX

    async def _invalidate_outstanding(
        self,
        user_id: str,
        db: AsyncSession,
        *,
        exclude_id: str | None = None,
        when: datetime | None = None,
    ) -> int:
        """Mark every outstanding reset token for ``user_id`` as used so it
        can no longer redeem a reset.

        Returns the count of rows that were actually invalidated. Used
        from three places: request_reset / resend_from_token (mark
        every prior outstanding row before issuing a new one — "newest
        wins"), and complete_reset (mark every other outstanding row
        once one is consumed — "stockpile cannot survive a redeem").

        Single-shot UPDATE rather than a SELECT-then-UPDATE; SQLAlchemy
        returns the affected rowcount and we read it for the audit log.
        """
        timestamp = when or datetime.now(UTC)
        conditions = [
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        ]
        if exclude_id is not None:
            conditions.append(PasswordResetToken.id != exclude_id)
        result = await db.execute(update(PasswordResetToken).where(*conditions).values(used_at=timestamp))
        return result.rowcount or 0

    def _build_reset_url(self, token: str) -> str:
        query = urlencode({"token": token})
        return f"{self._app_base_url}/reset-password?{query}"


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_password_reset_service_dependency(
    email_service: EmailService | None = None,
    cache: CacheBackend | None = None,
) -> PasswordResetService:
    """Construct PasswordResetService from settings + injected helpers.

    Tests can pass ``email_service`` and ``cache`` directly to bypass DI.
    """
    from ..auth.password_auth import password_auth_service
    from ..core.cache_backend import get_cache_backend_dependency
    from ..core.config import get_settings_instance
    from .email_service import get_email_service_dependency

    settings = get_settings_instance()
    return PasswordResetService(
        email_service=email_service if email_service is not None else get_email_service_dependency(),
        cache=cache if cache is not None else get_cache_backend_dependency(),
        password_validator=password_auth_service.validate_password,
        password_hasher=password_auth_service._hash_password,
        token_ttl_seconds=settings.password_reset_token_ttl_seconds,
        app_base_url=settings.app_base_url,
    )
