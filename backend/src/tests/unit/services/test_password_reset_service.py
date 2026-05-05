"""Unit tests for PasswordResetService (SHU-745).

Mirrors the structure of `test_email_verification_service.py`. Covers
token issuance, completion, expiry, single-use enforcement, the
no-enumeration request flow, and the token-as-identity recovery path.

The end-to-end flow (queue drain → email capture → complete) is in
`test_password_reset_flow.py`. These tests exercise the service surface
in isolation with mocked DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.services.password_reset_service import (
    PasswordPolicyError,
    PasswordResetService,
    RateLimitedError,
    TokenExpiredError,
    TokenInvalidError,
    _hash_token,
)


@pytest.fixture
def email_service() -> MagicMock:
    svc = MagicMock()
    svc.send = AsyncMock(return_value="audit-id-1")
    svc.check_rate_limit = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def cache() -> MagicMock:
    c = MagicMock()
    c.incr = AsyncMock(return_value=1)
    c.expire = AsyncMock(return_value=True)
    return c


def _accept_all_password(_p: str) -> list[str]:
    return []


def _hash_password(p: str) -> str:
    return f"hashed:{p}"


@pytest.fixture
def service(email_service: MagicMock, cache: MagicMock) -> PasswordResetService:
    return PasswordResetService(
        email_service=email_service,
        cache=cache,
        password_validator=_accept_all_password,
        password_hasher=_hash_password,
        token_ttl_seconds=3600,
        app_base_url="https://shu.example",
    )


def _make_user(**overrides: Any) -> MagicMock:
    user = MagicMock()
    user.id = overrides.get("id", "user-1")
    user.email = overrides.get("email", "user@example.com")
    user.name = overrides.get("name", "Alice")
    user.auth_method = overrides.get("auth_method", "password")
    user.password_hash = overrides.get("password_hash", "hashed:old")
    user.is_active = overrides.get("is_active", True)
    user.email_verified = overrides.get("email_verified", True)
    user.must_change_password = overrides.get("must_change_password", False)
    user.password_changed_at = overrides.get("password_changed_at", None)
    return user


def _make_token_row(**overrides: Any) -> MagicMock:
    row = MagicMock()
    row.id = overrides.get("id", "token-1")
    row.user_id = overrides.get("user_id", "user-1")
    row.token_hash = overrides.get("token_hash", _hash_token("plaintext-x"))
    row.expires_at = overrides.get("expires_at", datetime.now(UTC) + timedelta(seconds=3600))
    row.used_at = overrides.get("used_at", None)
    return row


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# request_reset
# ---------------------------------------------------------------------------


class TestRequestReset:
    @pytest.mark.asyncio
    async def test_creates_token_row_for_known_password_user(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        user = _make_user()
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.request_reset("user@example.com", "127.0.0.1", mock_db)

        # Token row was added to the session
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.user_id == "user-1"
        assert len(added.token_hash) == 64  # sha256 hex
        assert added.created_ip == "127.0.0.1"
        # Email queued
        email_service.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_email_no_op(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        await service.request_reset("nobody@example.com", None, mock_db)
        email_service.send.assert_not_called()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_sso_user_no_op(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        user = _make_user(auth_method="google", password_hash=None)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.request_reset("user@example.com", None, mock_db)
        email_service.send.assert_not_called()
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_inactive_user_no_op(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        user = _make_user(is_active=False)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db.execute.return_value = result

        await service.request_reset("user@example.com", None, mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_email_rate_limit_short_circuits_before_db_lookup(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        email_service.check_rate_limit = AsyncMock(return_value=False)

        await service.request_reset("user@example.com", None, mock_db)
        # Hit the cache (rate-limit) but never hit the DB and never sent
        mock_db.execute.assert_not_called()
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_ip_rate_limit_short_circuits_before_anything(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
        cache: MagicMock,
    ) -> None:
        # _consume_ip_bucket returns False when count > _REQUEST_PER_IP_MAX
        cache.incr = AsyncMock(return_value=999)

        await service.request_reset("user@example.com", "1.2.3.4", mock_db)
        # IP bucket short-circuit: per-email bucket never consulted, no
        # DB lookup, no send.
        email_service.check_rate_limit.assert_not_called()
        mock_db.execute.assert_not_called()
        email_service.send.assert_not_called()


# ---------------------------------------------------------------------------
# complete_reset
# ---------------------------------------------------------------------------


class TestCompleteReset:
    @pytest.mark.asyncio
    async def test_empty_token_rejected_immediately(
        self, service: PasswordResetService, mock_db: AsyncMock
    ) -> None:
        with pytest.raises(TokenInvalidError):
            await service.complete_reset("", "new-pass", mock_db)

    @pytest.mark.asyncio
    async def test_unknown_token_raises(
        self, service: PasswordResetService, mock_db: AsyncMock
    ) -> None:
        with pytest.raises(TokenInvalidError):
            await service.complete_reset("nope", "new-pass", mock_db)

    @pytest.mark.asyncio
    async def test_already_used_token_rejected(
        self, service: PasswordResetService, mock_db: AsyncMock
    ) -> None:
        token_row = _make_token_row(used_at=datetime.now(UTC) - timedelta(minutes=1))
        result = MagicMock()
        result.scalar_one_or_none.return_value = token_row
        mock_db.execute.return_value = result

        with pytest.raises(TokenInvalidError):
            await service.complete_reset("plaintext-x", "new-pass", mock_db)

    @pytest.mark.asyncio
    async def test_expired_token_raises_expired_subclass(
        self, service: PasswordResetService, mock_db: AsyncMock
    ) -> None:
        token_row = _make_token_row(
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = token_row
        mock_db.execute.return_value = result

        with pytest.raises(TokenExpiredError):
            await service.complete_reset("plaintext-x", "new-pass", mock_db)

    @pytest.mark.asyncio
    async def test_rate_limit_per_token_prefix(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        cache: MagicMock,
    ) -> None:
        # Simulate 6th attempt — over the cap of 5
        cache.incr = AsyncMock(return_value=6)

        with pytest.raises(RateLimitedError):
            await service.complete_reset("plaintext-x", "new-pass", mock_db)
        # Never reached the DB lookup
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_policy_failure_does_not_consume_token(
        self,
        email_service: MagicMock,
        cache: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        def _strict(p: str) -> list[str]:
            return [] if len(p) >= 8 else ["too short"]

        svc = PasswordResetService(
            email_service=email_service,
            cache=cache,
            password_validator=_strict,
            password_hasher=_hash_password,
            token_ttl_seconds=3600,
            app_base_url="https://shu.example",
        )

        token_row = _make_token_row(token_hash=_hash_token("plaintext-x"))
        user = _make_user()

        # Two successive db.execute calls: token lookup, then user lookup
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result])

        with pytest.raises(PasswordPolicyError):
            await svc.complete_reset("plaintext-x", "tiny", mock_db)

        # Token NOT marked used; user NOT mutated; password_changed_at NOT bumped
        assert token_row.used_at is None
        assert user.password_changed_at is None

    @pytest.mark.asyncio
    async def test_success_updates_password_and_marks_token_used(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
    ) -> None:
        """complete_reset writes the new hash, clears must_change_password,
        and marks the token used. password_changed_at is NOT verified here
        because the bump lives on the User model's @validates hook (see
        auth/models.py); MagicMock bypasses the ORM and so the hook does
        not fire on a mock user. The end-to-end flow test in
        test_password_reset_flow.py uses a real User and verifies the
        column gets bumped through the SQLAlchemy session.
        """
        token_row = _make_token_row(token_hash=_hash_token("plaintext-x"))
        user = _make_user(must_change_password=True)

        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        # Three db.execute calls: token lookup, user lookup, invalidate-others UPDATE
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result, MagicMock()])

        result = await service.complete_reset("plaintext-x", "any-new-pass", mock_db)
        assert result is user

        assert user.password_hash == "hashed:any-new-pass"
        assert user.must_change_password is False
        assert token_row.used_at is not None


# ---------------------------------------------------------------------------
# resend_from_token
# ---------------------------------------------------------------------------


class TestResendFromToken:
    @pytest.mark.asyncio
    async def test_empty_token_no_op(
        self, service: PasswordResetService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        await service.resend_from_token("", mock_db)
        mock_db.execute.assert_not_called()
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_token_no_op(
        self, service: PasswordResetService, mock_db: AsyncMock, email_service: MagicMock
    ) -> None:
        await service.resend_from_token("unknown", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sso_user_no_op(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        token_row = _make_token_row()
        user = _make_user(auth_method="google", password_hash=None)
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result])

        await service.resend_from_token("any", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_inactive_user_no_op(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        token_row = _make_token_row()
        user = _make_user(is_active=False)
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result])

        await service.resend_from_token("any", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_short_circuits_send(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        token_row = _make_token_row()
        user = _make_user()
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result])
        email_service.check_rate_limit = AsyncMock(return_value=False)

        await service.resend_from_token("any", mock_db)
        email_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_eligible_issues_fresh_token(
        self,
        service: PasswordResetService,
        mock_db: AsyncMock,
        email_service: MagicMock,
    ) -> None:
        token_row = _make_token_row()
        user = _make_user()
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_row
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        mock_db.execute = AsyncMock(side_effect=[token_result, user_result])

        await service.resend_from_token("any", mock_db)
        # New row added (separate from the original token row)
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.user_id == user.id
        # Email queued for the resolved user
        email_service.send.assert_awaited_once()
        sent_kwargs = email_service.send.call_args.kwargs
        assert sent_kwargs["to"] == user.email
