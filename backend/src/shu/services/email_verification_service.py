"""Email verification flow for password-registered users (SHU-507).

Generates short-lived verification tokens, sends them via the SHU-508
EmailService, and validates them on submission. Token plaintext only ever
appears in the verification email — the database stores a sha256 hash so
a DB compromise does not yield live tokens.

State transitions on the User row:

* `issue_token`  → writes token_hash + expires_at, queues `verify_email` send
* `verify_token` → sets email_verified=True, clears token_hash + expires_at
* `resend`       → rate-limit check → issue_token (or no-op for unknown /
                   already-verified addresses, no enumeration)

Three columns on the user row, not a separate token table — at most one
outstanding verification per user is the invariant we want, and resend
overwrites. See the SHU-507 ticket Notes for the full rationale.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.logging import get_logger
from .email_service import EmailService

logger = get_logger(__name__)


# Rate-limit policy for resend-verification: SHU-507 AC says max 3 per hour
# per recipient. The numbers live here (the policy layer) rather than in
# EmailService (the helper layer).
_RESEND_MAX_PER_WINDOW = 3
_RESEND_WINDOW_SECONDS = 3600


class EmailVerificationError(Exception):
    """Base error raised by the verification service for caller-visible problems."""


class TokenInvalidError(EmailVerificationError):
    """Raised when the supplied token does not match any user, has expired,
    or has already been consumed. Callers should map this to a 400.
    """


class TokenExpiredError(TokenInvalidError):
    """Raised specifically when the supplied token matched a real row but
    has passed its expiry. Subclasses ``TokenInvalidError`` so existing
    ``except TokenInvalidError`` blocks continue to catch it; the email
    attribute lets the endpoint surface a "expired, here's your address —
    click resend" UX without making the user retype.

    The hash match is what makes returning the email safe — by definition
    the caller already had the token for this user, so the address is
    not new information being leaked.
    """

    def __init__(self, message: str, *, email: str) -> None:
        super().__init__(message)
        self.email = email


def _hash_token(plaintext: str) -> str:
    """Hash a verification token for at-rest storage. sha256 hex (64 chars)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class EmailVerificationService:
    """Owns issuing, verifying, and resending email-verification tokens."""

    def __init__(
        self,
        *,
        email_service: EmailService,
        token_ttl_seconds: int,
        app_base_url: str,
    ) -> None:
        self._email = email_service
        self._token_ttl = token_ttl_seconds
        self._app_base_url = app_base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def issue_token(self, user: User, db: AsyncSession) -> str:
        """Generate a verification token, persist its hash, queue the email.

        Returns the plaintext token. Production callers ignore the return
        (the value goes into the email URL); tests use it to drive the
        verify endpoint without parsing email content.

        If a verification is already pending on the user row, it is
        overwritten — at most one outstanding token per user.
        """
        plaintext = secrets.token_urlsafe(32)
        user.email_verification_token_hash = _hash_token(plaintext)
        user.email_verification_expires_at = datetime.now(UTC) + timedelta(seconds=self._token_ttl)
        await db.flush()

        verification_url = self._build_verification_url(plaintext)
        await self._email.send(
            db=db,
            template_name="verify_email",
            to=user.email,
            context={
                "name": user.name,
                "verification_url": verification_url,
                "expires_in_hours": max(1, self._token_ttl // 3600),
            },
            idempotency_key=f"verify_email:{user.id}:{user.email_verification_token_hash}",
        )

        logger.info(
            "Verification token issued for %s (user %s); expires in %ds",
            user.email,
            user.id,
            self._token_ttl,
            extra={
                "event": "verification.issued",
                "user_id": user.id,
                "email": user.email,
                "ttl_seconds": self._token_ttl,
            },
        )
        return plaintext

    async def verify_token(self, token: str, db: AsyncSession) -> User:
        """Validate a plaintext token and mark the user verified.

        Single-use: the token columns are cleared on success so a leaked
        link cannot be replayed. Single-use also covers the "already
        verified" case — a second valid POST returns the same row but
        the columns are already null, which surfaces as TokenInvalidError.

        Raises TokenInvalidError on:
        - unknown token (no user has this hash)
        - expired token
        - column already cleared (used)
        """
        if not token:
            raise TokenInvalidError("token is required")

        token_hash = _hash_token(token)
        stmt = select(User).where(User.email_verification_token_hash == token_hash)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            logger.info(
                "Verification failed: unknown token (no user matches the supplied token hash; "
                "either the token never existed, was already consumed, or was overwritten "
                "by a newer token from a resend)",
                extra={"event": "verification.attempt_failed", "reason": "unknown_token"},
            )
            raise TokenInvalidError("verification token is invalid")

        # The columns may be set but stale — check expiry. The column is
        # TIMESTAMP with timezone, but some drivers (notably SQLite via
        # aiosqlite) strip timezone info on read. Normalize to UTC-aware
        # before comparison so the gate behaves consistently regardless
        # of which driver is in use.
        if user.email_verification_expires_at is None:
            # Should not happen — hash present, expiry NULL — but defensive.
            logger.warning(
                "Verification failed for %s (user %s): token row has expiry=NULL "
                "(unexpected DB state — investigate)",
                user.email,
                user.id,
                extra={
                    "event": "verification.attempt_failed",
                    "reason": "expiry_null",
                    "user_id": user.id,
                    "email": user.email,
                },
            )
            raise TokenInvalidError("verification token is invalid")
        expires_at = user.email_verification_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires_at:
            logger.info(
                "Verification failed for %s (user %s): token expired at %s",
                user.email,
                user.id,
                expires_at.isoformat(),
                extra={
                    "event": "verification.attempt_failed",
                    "reason": "expired",
                    "user_id": user.id,
                    "email": user.email,
                    "expired_at": expires_at.isoformat(),
                },
            )
            raise TokenExpiredError("verification token has expired", email=user.email)

        user.email_verified = True
        user.email_verification_token_hash = None
        user.email_verification_expires_at = None
        await db.flush()

        logger.info(
            "Email verified for %s (user %s) — account is now eligible to log in",
            user.email,
            user.id,
            extra={
                "event": "verification.completed",
                "user_id": user.id,
                "email": user.email,
            },
        )
        return user

    async def resend(self, email: str, db: AsyncSession) -> None:
        """Re-issue a verification token for `email`.

        Always returns None. Does NOT distinguish between unknown email,
        already-verified email, and rate-limited resend — the response is
        identical in all cases so the endpoint cannot be used for
        enumeration.
        """
        # Rate-limit FIRST so unknown / verified addresses cannot be probed
        # at unlimited speed for timing differences.
        allowed = await self._email.check_rate_limit(
            template_name="verify_email",
            to=email,
            max_per_window=_RESEND_MAX_PER_WINDOW,
            window_seconds=_RESEND_WINDOW_SECONDS,
        )
        if not allowed:
            # Note: we don't log the email at WARNING because the rate-limit
            # surface is anti-enumeration — log too aggressively here and
            # an attacker can probe addresses by watching the log feed.
            # INFO with just the bucket fingerprint is enough for ops.
            logger.info(
                "Verification resend rate-limited (max %d in %ds for this address)",
                _RESEND_MAX_PER_WINDOW,
                _RESEND_WINDOW_SECONDS,
                extra={"event": "verification.resend_rate_limited"},
            )
            return

        # Case-insensitive lookup mirroring user_service.get_user_auth_method:
        # email is stored verbatim at registration, but tooling and forms
        # produce mixed-case input ("Mike@x.com" vs "mike@x.com"); the
        # canonical lookup folds case so the resend doesn't 404 on a
        # casing mismatch.
        stmt = select(User).where(func.lower(User.email) == email.lower())
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            # Unknown user — no-op so an attacker cannot distinguish.
            # Logged so ops can correlate "user reports they didn't get email"
            # with "their address never reached our DB" (typo at registration).
            logger.info(
                "Verification resend requested for unknown address %s — no-op (no enumeration)",
                email,
                extra={"event": "verification.resend_unknown_address", "email": email},
            )
            return

        if user.auth_method != "password":
            logger.info(
                "Verification resend requested for %s but the account uses %s auth — no-op",
                email,
                user.auth_method,
                extra={
                    "event": "verification.resend_wrong_auth_method",
                    "email": email,
                    "auth_method": user.auth_method,
                },
            )
            return

        if user.email_verified:
            logger.info(
                "Verification resend requested for %s but the account is already verified — no-op",
                email,
                extra={"event": "verification.resend_already_verified", "email": email},
            )
            return

        logger.info(
            "Verification resend issuing new token for %s (user %s)",
            user.email,
            user.id,
            extra={
                "event": "verification.resend_issuing",
                "email": user.email,
                "user_id": user.id,
            },
        )
        await self.issue_token(user, db)

    async def resend_from_token(self, token: str, db: AsyncSession) -> None:
        """Re-issue a verification token using an old token as the identity.

        The verify-email page knows the original token from the URL even
        when the token has expired, because the token is what got them to
        the page in the first place. This method lets the page request a
        fresh email by handing the *old* token back to the server — the
        server hashes it, looks up the user, and issues a fresh token. The
        user never has to type or see their email address.

        Always returns None. Silent on all failure branches (no
        enumeration). The token-as-identity model means the only way an
        attacker can trigger a send is to already possess a token for the
        target user — so triggering one re-sends to the legitimate owner,
        which is benign.
        """
        if not token:
            return

        token_hash = _hash_token(token)
        stmt = select(User).where(User.email_verification_token_hash == token_hash)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            # Token not in the DB — either fully unknown, or it was the
            # current token and got overwritten by a more recent resend.
            # Either way, silent no-op (no enumeration). The frontend
            # shows a generic "if the link belonged to a pending account,
            # a new email has been sent" message regardless.
            logger.info(
                "Token-based resend requested for an unknown token hash — no-op",
                extra={"event": "verification.resend_from_token_unknown"},
            )
            return

        if user.email_verified:
            logger.info(
                "Token-based resend requested for %s but the account is already verified — no-op",
                user.email,
                extra={
                    "event": "verification.resend_from_token_already_verified",
                    "email": user.email,
                    "user_id": user.id,
                },
            )
            return

        if user.auth_method != "password":
            logger.info(
                "Token-based resend requested for %s but the account uses %s auth — no-op",
                user.email,
                user.auth_method,
                extra={
                    "event": "verification.resend_from_token_wrong_auth_method",
                    "email": user.email,
                    "user_id": user.id,
                    "auth_method": user.auth_method,
                },
            )
            return

        # Rate-limit on the recipient address — same bucket the email-based
        # resend uses, so the per-user limit is consistent regardless of
        # which surface the user came in through.
        allowed = await self._email.check_rate_limit(
            template_name="verify_email",
            to=user.email,
            max_per_window=_RESEND_MAX_PER_WINDOW,
            window_seconds=_RESEND_WINDOW_SECONDS,
        )
        if not allowed:
            logger.info(
                "Token-based resend rate-limited for %s",
                user.email,
                extra={
                    "event": "verification.resend_from_token_rate_limited",
                    "email": user.email,
                    "user_id": user.id,
                },
            )
            return

        logger.info(
            "Token-based resend issuing new token for %s (user %s) — old token was %s",
            user.email,
            user.id,
            "expired"
            if user.email_verification_expires_at
            and datetime.now(UTC)
            > (
                user.email_verification_expires_at.replace(tzinfo=UTC)
                if user.email_verification_expires_at.tzinfo is None
                else user.email_verification_expires_at
            )
            else "still valid",
            extra={
                "event": "verification.resend_from_token_issuing",
                "email": user.email,
                "user_id": user.id,
            },
        )
        await self.issue_token(user, db)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_verification_url(self, token: str) -> str:
        # urlencode handles any URL-unsafe chars secrets.token_urlsafe might
        # produce (it should not — token_urlsafe is base64 url-safe — but
        # we encode defensively so a future token-format change doesn't
        # silently break links).
        query = urlencode({"token": token})
        return f"{self._app_base_url}/verify-email?{query}"


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_email_verification_service_dependency(
    email_service: EmailService | None = None,
) -> EmailVerificationService:
    """Construct EmailVerificationService from settings + the email service.

    Tests can pass `email_service` directly to bypass DI.
    """
    from ..core.config import get_settings_instance
    from .email_service import get_email_service_dependency

    settings = get_settings_instance()
    return EmailVerificationService(
        email_service=email_service if email_service is not None else get_email_service_dependency(),
        token_ttl_seconds=settings.email_verification_token_ttl_seconds,
        app_base_url=settings.app_base_url,
    )
