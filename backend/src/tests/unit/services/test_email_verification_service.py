"""Unit tests for EmailVerificationService (SHU-507).

Covers token issuance, verification, expiry, single-use enforcement, and
the no-enumeration resend flow. Wiring into the register endpoint and the
login gate are exercised by separate auth-flow tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.services.email_verification_service import (
    EmailVerificationService,
    TokenInvalidError,
    _hash_token,
)


@pytest.fixture
def email_service() -> MagicMock:
    svc = MagicMock()
    svc.send = AsyncMock(return_value="audit-id-123")
    svc.check_rate_limit = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def service(email_service: MagicMock) -> EmailVerificationService:
    return EmailVerificationService(
        email_service=email_service,
        token_ttl_seconds=86400,
        app_base_url="https://shu.example",
    )


def _make_user(**overrides: Any) -> MagicMock:
    user = MagicMock()
    user.id = overrides.get("id", "user-1")
    user.email = overrides.get("email", "user@example.com")
    user.name = overrides.get("name", "Alice")
    user.auth_method = overrides.get("auth_method", "password")
    user.email_verified = overrides.get("email_verified", False)
    user.email_verification_token_hash = overrides.get("email_verification_token_hash")
    user.email_verification_expires_at = overrides.get("email_verification_expires_at")
    return user


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# issue_token
# ---------------------------------------------------------------------------


class TestIssueToken:
    @pytest.mark.asyncio
    async def test_writes_hash_not_plaintext(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user()
        plaintext = await service.issue_token(user, mock_db)

        assert isinstance(plaintext, str)
        assert len(plaintext) >= 32
        # Stored value is the hash; plaintext is NEVER persisted to the user row
        assert user.email_verification_token_hash == _hash_token(plaintext)
        assert user.email_verification_token_hash != plaintext
        assert len(user.email_verification_token_hash) == 64  # sha256 hex

    @pytest.mark.asyncio
    async def test_sets_expiry_in_future(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        user = _make_user()
        before = datetime.now(UTC)
        await service.issue_token(user, mock_db)
        after = datetime.now(UTC)

        assert user.email_verification_expires_at is not None
        # Expires roughly 24h after now (token_ttl_seconds=86400)
        delta = user.email_verification_expires_at - before
        assert timedelta(hours=23, minutes=59) < delta < timedelta(hours=24, minutes=1)
        # Sanity: expiry is also after the post-issue timestamp
        assert user.email_verification_expires_at > after

    @pytest.mark.asyncio
    async def test_enqueues_email_with_verification_url(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user()
        plaintext = await service.issue_token(user, mock_db)

        email_service.send.assert_awaited_once()
        kwargs = email_service.send.await_args.kwargs
        assert kwargs["template_name"] == "verify_email"
        assert kwargs["to"] == "user@example.com"
        ctx = kwargs["context"]
        assert ctx["name"] == "Alice"
        assert plaintext in ctx["verification_url"]
        assert ctx["verification_url"].startswith("https://shu.example/verify-email?token=")
        assert ctx["expires_in_hours"] == 24
        # Idempotency key prevents double-enqueue if the same hash gets retried
        assert kwargs["idempotency_key"].startswith("verify_email:user-1:")

    @pytest.mark.asyncio
    async def test_overwrites_existing_pending_token(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        user = _make_user(
            email_verification_token_hash="old-hash" * 8,  # 64 chars but irrelevant
            email_verification_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        plaintext = await service.issue_token(user, mock_db)
        # New token replaces old, regardless of whether old was expired
        assert user.email_verification_token_hash == _hash_token(plaintext)
        assert user.email_verification_expires_at > datetime.now(UTC)


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_success_clears_token_columns_and_marks_verified(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        plaintext = "abc-test-token"
        user = _make_user(
            email_verification_token_hash=_hash_token(plaintext),
            email_verification_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        verified_user = await service.verify_token(plaintext, mock_db)

        assert verified_user is user
        assert user.email_verified is True
        assert user.email_verification_token_hash is None
        assert user.email_verification_expires_at is None

    @pytest.mark.asyncio
    async def test_unknown_token_raises(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        # Default mock_db.execute returns None — no user matches
        with pytest.raises(TokenInvalidError):
            await service.verify_token("not-a-real-token", mock_db)

    @pytest.mark.asyncio
    async def test_expired_token_raises(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        plaintext = "expired-token"
        user = _make_user(
            email_verification_token_hash=_hash_token(plaintext),
            email_verification_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        with pytest.raises(TokenInvalidError, match="expired"):
            await service.verify_token(plaintext, mock_db)
        # Expired token must NOT mark the user verified
        assert user.email_verified is False

    @pytest.mark.asyncio
    async def test_empty_token_raises_immediately(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        with pytest.raises(TokenInvalidError):
            await service.verify_token("", mock_db)
        # Empty token short-circuits — no DB lookup
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_replay_after_use_raises(
        self, service: EmailVerificationService, mock_db: AsyncMock
    ) -> None:
        """A second verify with the same token finds no row (hash cleared)."""
        plaintext = "single-use-token"
        # Simulate post-success state: the row's hash was cleared, so a second
        # verify with the same token finds no match.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        with pytest.raises(TokenInvalidError):
            await service.verify_token(plaintext, mock_db)


# ---------------------------------------------------------------------------
# resend
# ---------------------------------------------------------------------------


class TestResend:
    @pytest.mark.asyncio
    async def test_pending_user_re_issues(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(email_verified=False)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend("user@example.com", mock_db)
        # Token issued, email queued
        assert user.email_verification_token_hash is not None
        email_service.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_email_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        # Default mock returns None — unknown user
        await service.resend("unknown@example.com", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_verified_user_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(email_verified=True)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend("user@example.com", mock_db)
        # Send not called — already verified is a silent no-op
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sso_only_user_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(auth_method="google")
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend("user@example.com", mock_db)
        # SSO accounts cannot resend a password-flow verification — no enumeration
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_hit_short_circuits(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        email_service.check_rate_limit = AsyncMock(return_value=False)

        await service.resend("user@example.com", mock_db)
        # Rate-limit failure prevents both DB lookup AND send — anti-enumeration
        # at unlimited speed.
        mock_db.execute.assert_not_called()
        email_service.send.assert_not_called()


# ---------------------------------------------------------------------------
# resend_from_token — token-as-identity recovery (no email retype)
# ---------------------------------------------------------------------------


class TestResendFromToken:
    """Recovery path the verify-email page uses when a user clicks an expired
    link. The page already has the (stale) token; handing it back to the
    server lets us resolve the user from its hash and issue a fresh token —
    the user never has to type, see, or know their email address.
    """

    @pytest.mark.asyncio
    async def test_pending_user_re_issues(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(email_verified=False)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend_from_token("any-token", mock_db)
        # Fresh token issued, email queued via the same `issue_token` path
        assert user.email_verification_token_hash is not None
        email_service.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_token_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        await service.resend_from_token("", mock_db)
        # Silent no-op — never even hit the DB
        mock_db.execute.assert_not_called()
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_token_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        # Default mock returns None — token hash matches no row (either
        # never existed or was overwritten by a more recent resend).
        await service.resend_from_token("unknown-token", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_verified_user_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(email_verified=True)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend_from_token("any-token", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sso_only_user_no_op(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        user = _make_user(auth_method="google")
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.resend_from_token("any-token", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_hit_short_circuits_send(
        self, service: EmailVerificationService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        # Token-based resend gates the recipient bucket AFTER the user
        # lookup (unlike email-based resend which gates first) — the user
        # is already identified by the hash match, so timing-based
        # enumeration is not a concern. Rate-limit still blocks the send.
        user = _make_user(email_verified=False)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result
        email_service.check_rate_limit = AsyncMock(return_value=False)

        await service.resend_from_token("any-token", mock_db)
        email_service.send.assert_not_called()
