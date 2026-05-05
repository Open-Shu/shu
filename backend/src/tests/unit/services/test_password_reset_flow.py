"""End-to-end flow test for SHU-745 password reset.

Exercises the full path: `request_reset` → audit row + queued send →
`process_email_queue_now()` drains the queue → `ConsoleEmailBackend`
captures the email → token extraction → `complete_reset` updates the
password, marks the token used, and bumps `password_changed_at`.

Mirrors `test_verification_flow.py`. SQLite in-memory; the partial /
named indexes are silently dropped on SQLite — fine here because we
exercise the lookups, not the indexes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shu.auth.models import User
from shu.core.cache_backend import InMemoryCacheBackend
from shu.core.email import ConsoleEmailBackend
from shu.core.queue_backend import InMemoryQueueBackend
from shu.models.email_send_log import EmailSendLog
from shu.models.password_reset_token import PasswordResetToken
from shu.services.email_service import EmailService
from shu.services.password_reset_service import (
    PasswordPolicyError,
    PasswordResetService,
    TokenInvalidError,
)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(lambda sync_conn: EmailSendLog.__table__.create(sync_conn, checkfirst=True))
        await conn.run_sync(lambda sync_conn: User.__table__.create(sync_conn, checkfirst=True))
        await conn.run_sync(lambda sync_conn: PasswordResetToken.__table__.create(sync_conn, checkfirst=True))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    root = tmp_path / "templates" / "email"
    (root / "password_reset").mkdir(parents=True)
    (root / "_base.txt").write_text("{%- block content %}{% endblock -%}\n")
    (root / "password_reset" / "subject.txt").write_text("Reset {{ name }}")
    (root / "password_reset" / "body.txt").write_text(
        "{% extends '_base.txt' %}{% block content %}Hi {{ name }}, click {{ reset_url }}.{% endblock %}"
    )
    return root


@pytest.fixture
def queue() -> InMemoryQueueBackend:
    return InMemoryQueueBackend()


@pytest.fixture
def cache() -> InMemoryCacheBackend:
    return InMemoryCacheBackend()


@pytest.fixture
def email_service(
    templates_dir: Path, queue: InMemoryQueueBackend, cache: InMemoryCacheBackend
) -> EmailService:
    return EmailService(
        queue=queue,
        cache=cache,
        from_address="noreply@example.com",
        from_name="Shu",
        templates_root=templates_dir,
    )


def _trivial_validator(_password: str) -> list[str]:
    """Test-only validator that accepts any password."""
    return []


def _trivial_hasher(password: str) -> str:
    """Test-only hasher that prefixes the plaintext for visibility."""
    return f"hashed:{password}"


def _strict_validator(password: str) -> list[str]:
    """Test-only minimal-rule validator: at least 8 chars."""
    if len(password) < 8:
        return ["Password must be at least 8 characters long"]
    return []


@pytest.fixture
def reset_service(email_service: EmailService, cache: InMemoryCacheBackend) -> PasswordResetService:
    return PasswordResetService(
        email_service=email_service,
        cache=cache,
        password_validator=_trivial_validator,
        password_hasher=_trivial_hasher,
        token_ttl_seconds=3600,
        app_base_url="https://shu.example",
    )


def _extract_token_from_url(url: str) -> str:
    # token_urlsafe produces base64-url chars: alphanumerics plus `-` and `_`.
    # Anchoring on those prevents regex greediness from swallowing trailing
    # punctuation in the email body (e.g. the period after the link).
    match = re.search(r"token=([A-Za-z0-9_-]+)", url)
    assert match, f"no token in url: {url}"
    return match.group(1)


@pytest.mark.asyncio
async def test_full_reset_flow(
    reset_service: PasswordResetService,
    queue: InMemoryQueueBackend,
    session_factory,
) -> None:
    """End-to-end: request → queue drain → captured email → token extracted →
    complete_reset → password updated, token consumed, password_changed_at set.
    """
    # 1. Existing active password user.
    async with session_factory() as session:
        user = User(
            email="alice@example.com",
            name="Alice",
            password_hash="hashed:old-password",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        user_id = user.id

    # 2. Request a reset (analogue of POST /auth/request-password-reset).
    async with session_factory() as session:
        await reset_service.request_reset("alice@example.com", "127.0.0.1", session)
        await session.commit()

    # 3. Drain the email queue — runs the worker handler against
    #    ConsoleEmailBackend, which captures the rendered body.
    captured: list[str] = []

    class _CapturingBackend(ConsoleEmailBackend):
        async def send_email(self, message):  # type: ignore[override]
            captured.append(message.body_text or "")
            return await super().send_email(message)

    backend = _CapturingBackend()

    async def _get_backend():
        return backend

    from tests.integ.email.queue_drain import process_email_queue_now

    with (
        patch("shu.email_handler.get_email_backend", side_effect=_get_backend),
        patch("shu.email_handler.get_async_session_local", return_value=session_factory),
    ):
        processed = await process_email_queue_now(queue)

    assert processed == 1
    assert len(captured) == 1
    plaintext_token = _extract_token_from_url(captured[0])

    # 4. Token row exists, is unconsumed, expires in the future.
    async with session_factory() as session:
        token_row = (
            await session.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        ).scalar_one()
        assert token_row.used_at is None
        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        assert expires_at > datetime.now(UTC)
        assert token_row.created_ip == "127.0.0.1"

    # 5. Audit row reflects sent state.
    async with session_factory() as session:
        audit = (
            await session.execute(select(EmailSendLog).where(EmailSendLog.template_name == "password_reset"))
        ).scalar_one()
        assert audit.status == "sent"
        assert audit.backend_name == "console"

    # 6. Complete the reset (analogue of POST /auth/reset-password).
    async with session_factory() as session:
        before = datetime.now(UTC)
        updated = await reset_service.complete_reset(plaintext_token, "new-strong-pass", session)
        await session.commit()
        assert updated.password_hash == "hashed:new-strong-pass"

    # 7. Final state: password updated, token consumed, password_changed_at set.
    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert u.password_hash == "hashed:new-strong-pass"
        assert u.must_change_password is False
        password_changed_at = u.password_changed_at
        if password_changed_at is not None and password_changed_at.tzinfo is None:
            password_changed_at = password_changed_at.replace(tzinfo=UTC)
        assert password_changed_at is not None
        assert password_changed_at >= before

        token_row = (
            await session.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        ).scalar_one()
        assert token_row.used_at is not None


@pytest.mark.asyncio
async def test_replay_after_reset_rejected(
    reset_service: PasswordResetService,
    session_factory,
) -> None:
    """A second reset with the same token after success is rejected (single-use)."""
    async with session_factory() as session:
        user = User(
            email="bob@example.com",
            name="Bob",
            password_hash="hashed:old",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        await session.commit()

    async with session_factory() as session:
        await reset_service.request_reset("bob@example.com", None, session)
        await session.commit()

    # Pull the plaintext via service round-trip — the test fixture's
    # in-memory queue isn't drained here, so we extract directly from the
    # db row by re-issuing through the service. Simpler: skip the queue
    # for this test and have the service issue a fresh token whose
    # plaintext we capture by patching secrets.token_urlsafe.
    # (Cleaner: mirror the full flow as in test_full_reset_flow.)
    from shu.services.password_reset_service import _hash_token

    # Use the row's hash to reconstruct: we need the plaintext, so issue
    # a deterministic token by patching.
    async with session_factory() as session:
        await session.execute(
            PasswordResetToken.__table__.delete()
        )
        await session.commit()

    deterministic = "deterministic-test-token"
    with patch("shu.services.password_reset_service.secrets.token_urlsafe", return_value=deterministic):
        async with session_factory() as session:
            await reset_service.request_reset("bob@example.com", None, session)
            await session.commit()

    # First reset succeeds.
    async with session_factory() as session:
        await reset_service.complete_reset(deterministic, "new-strong-pass", session)
        await session.commit()

    # Second reset with the same token is rejected (used_at is set).
    async with session_factory() as session:
        with pytest.raises(TokenInvalidError):
            await reset_service.complete_reset(deterministic, "another-pass", session)


@pytest.mark.asyncio
async def test_password_policy_failure_does_not_consume_token(
    email_service: EmailService,
    cache: InMemoryCacheBackend,
    session_factory,
) -> None:
    """A reset with a policy-failing password rejects, leaves the token
    unconsumed, and does not bump password_changed_at — the user can
    retry with a stronger password without requesting a new email.
    """
    service = PasswordResetService(
        email_service=email_service,
        cache=cache,
        password_validator=_strict_validator,
        password_hasher=_trivial_hasher,
        token_ttl_seconds=3600,
        app_base_url="https://shu.example",
    )

    async with session_factory() as session:
        user = User(
            email="carol@example.com",
            name="Carol",
            password_hash="hashed:old",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        user_id = user.id

    deterministic = "policy-test-token"
    with patch("shu.services.password_reset_service.secrets.token_urlsafe", return_value=deterministic):
        async with session_factory() as session:
            await service.request_reset("carol@example.com", None, session)
            await session.commit()

    # Submit a too-short password — policy validator rejects.
    async with session_factory() as session:
        with pytest.raises(PasswordPolicyError):
            await service.complete_reset(deterministic, "short", session)
        # Test fixture isolates each session; the rejection didn't commit.

    # Token is still usable; user can retry with a longer password.
    async with session_factory() as session:
        await service.complete_reset(deterministic, "longer-pass", session)
        await session.commit()

    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert u.password_hash == "hashed:longer-pass"


@pytest.mark.asyncio
async def test_unknown_email_is_silent_no_op(
    reset_service: PasswordResetService,
    session_factory,
) -> None:
    """A reset request for an address that does not exist returns None
    silently and does not create a token row (no enumeration).
    """
    async with session_factory() as session:
        await reset_service.request_reset("nobody@example.com", None, session)
        await session.commit()

    async with session_factory() as session:
        rows = (await session.execute(select(PasswordResetToken))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_validator_bumps_password_changed_at_on_any_password_hash_set(
    session_factory,
) -> None:
    """SHU-745 session-invalidation primitive: User.password_hash has a
    @validates hook that bumps password_changed_at on every attribute set.
    This catches paths the reset service doesn't go through —
    change_password, admin reset_password, future password mutations —
    so the JWT iat-vs-password_changed_at gate fires uniformly regardless
    of which code path mutated the column.
    """
    # Initial creation sets password_hash → hook fires → password_changed_at
    # is set to creation time. (For a brand-new account this is harmless:
    # the user hasn't logged in yet, no JWT exists to invalidate.)
    async with session_factory() as session:
        user = User(
            email="hookcheck@example.com",
            name="Hook",
            password_hash="hashed:original",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        original_pca = user.password_changed_at
        assert original_pca is not None
        user_id = user.id

    # A subsequent UPDATE that touches password_hash via the ORM bumps
    # the column. The bump uses now() so the new value is strictly later
    # than the original.
    import asyncio

    await asyncio.sleep(0.01)  # ensure now() advances past original_pca

    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        u.password_hash = "hashed:rotated"
        await session.commit()

    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        new_pca = u.password_changed_at
        assert new_pca is not None
        # Normalize both sides to UTC-aware (SQLite drops tz on read).
        if new_pca.tzinfo is None:
            new_pca = new_pca.replace(tzinfo=UTC)
        if original_pca.tzinfo is None:
            original_pca = original_pca.replace(tzinfo=UTC)
        assert new_pca > original_pca

    # Updating a non-password column does NOT bump password_changed_at —
    # the @validates hook is scoped to password_hash specifically.
    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        before_unrelated_update = u.password_changed_at
        u.name = "Renamed"
        await session.commit()

    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert u.password_changed_at == before_unrelated_update


@pytest.mark.asyncio
async def test_sso_user_request_is_silent_no_op(
    reset_service: PasswordResetService,
    session_factory,
) -> None:
    """A reset request for an SSO-only user returns None silently and
    does not create a token row.
    """
    async with session_factory() as session:
        user = User(
            email="dan@example.com",
            name="Dan",
            password_hash=None,
            auth_method="google",
            role="regular_user",
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.commit()

    async with session_factory() as session:
        await reset_service.request_reset("dan@example.com", None, session)
        await session.commit()

    async with session_factory() as session:
        rows = (await session.execute(select(PasswordResetToken))).scalars().all()
        assert rows == []
