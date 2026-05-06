"""End-to-end flow test for SHU-507 email verification.

Exercises the full path: `EmailVerificationService.issue_token` → audit
row + queued send → `process_email_queue_now()` drains the queue →
`ConsoleEmailBackend` captures the email → `verify_token` flips the user
to `email_verified=True` and clears the token columns.

Uses SQLite in-memory rather than Postgres for the same reason as
`test_email_flow.py`: this test cares about service wiring + state
transitions, not Postgres-specific features. The partial index on
`email_verification_token_hash` (created by migration 008_0014 with
`postgresql_where`) is silently dropped on SQLite — fine here because we
are not exercising the index, only the lookup.
"""

from __future__ import annotations

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
from shu.services.email_service import EmailService
from shu.services.email_verification_service import EmailVerificationService


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        # Create only the tables this test needs. Pulling all models via
        # Base.metadata.create_all surfaces unrelated dialect issues
        # (Postgres-specific column types in other models).
        await conn.run_sync(
            lambda sync_conn: EmailSendLog.__table__.create(sync_conn, checkfirst=True)
        )
        await conn.run_sync(
            lambda sync_conn: User.__table__.create(sync_conn, checkfirst=True)
        )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    root = tmp_path / "templates" / "email"
    (root / "verify_email").mkdir(parents=True)
    (root / "_base.txt").write_text("{%- block content %}{% endblock -%}\n")
    (root / "verify_email" / "subject.txt").write_text("Verify {{ name }}")
    (root / "verify_email" / "body.txt").write_text(
        "{% extends '_base.txt' %}{% block content %}Hi {{ name }}, click {{ verification_url }}.{% endblock %}"
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


@pytest.fixture
def verification_service(email_service: EmailService) -> EmailVerificationService:
    return EmailVerificationService(
        email_service=email_service,
        token_ttl_seconds=86400,
        app_base_url="https://shu.example",
    )


@pytest.mark.asyncio
async def test_full_verification_flow(
    verification_service: EmailVerificationService,
    queue: InMemoryQueueBackend,
    session_factory,
) -> None:
    """End-to-end: issue → queue drain → captured email → verify → user verified."""
    # 1. Create the unverified user (analogue of register endpoint with
    #    requires_email_verification=True).
    async with session_factory() as session:
        user = User(
            email="alice@example.com",
            name="Alice",
            password_hash="not-a-real-hash",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=False,
        )
        session.add(user)
        await session.flush()

        # 2. Issue verification token (writes hash + expiry, queues email)
        plaintext = await verification_service.issue_token(user, session)
        await session.commit()
        user_id = user.id

    # 3. Drain the email queue — runs the worker handler against
    #    ConsoleEmailBackend, which logs the message rather than sending.
    backend = ConsoleEmailBackend()

    async def _get_backend():
        return backend

    from tests.integ.email.queue_drain import process_email_queue_now

    with (
        patch("shu.email_handler.get_email_backend", side_effect=_get_backend),
        patch("shu.email_handler.get_async_session_local", return_value=session_factory),
    ):
        processed = await process_email_queue_now(queue)

    assert processed == 1

    # 4. Audit row reflects sent state (delivered to console backend)
    async with session_factory() as session:
        audit = (
            await session.execute(
                select(EmailSendLog).where(EmailSendLog.template_name == "verify_email")
            )
        ).scalar_one()
        assert audit.status == "sent"
        assert audit.backend_name == "console"
        assert audit.provider_message_id is not None

    # 5. User starts unverified with token columns set. Note: SQLite drops
    #    timezone info from TIMESTAMP columns; we normalise to compare.
    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert u.email_verified is False
        assert u.email_verification_token_hash is not None
        assert u.email_verification_expires_at is not None
        expires_at = u.email_verification_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        assert expires_at > datetime.now(UTC)

    # 6. Verify the token (analogue of POST /auth/verify-email)
    async with session_factory() as session:
        verified_user = await verification_service.verify_token(plaintext, session)
        assert verified_user.id == user_id
        await session.commit()

    # 7. Final state: verified, columns cleared
    async with session_factory() as session:
        u = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert u.email_verified is True
        assert u.email_verification_token_hash is None
        assert u.email_verification_expires_at is None


@pytest.mark.asyncio
async def test_replay_after_verification_rejected(
    verification_service: EmailVerificationService,
    queue: InMemoryQueueBackend,
    session_factory,
) -> None:
    """A second verify with the same token after success is rejected (single-use)."""
    from shu.services.email_verification_service import TokenInvalidError

    async with session_factory() as session:
        user = User(
            email="bob@example.com",
            name="Bob",
            password_hash="x",
            auth_method="password",
            role="regular_user",
            is_active=True,
            email_verified=False,
        )
        session.add(user)
        await session.flush()
        plaintext = await verification_service.issue_token(user, session)
        await session.commit()

    # First verify succeeds
    async with session_factory() as session:
        await verification_service.verify_token(plaintext, session)
        await session.commit()

    # Second verify with the same token must be rejected — the token columns
    # were cleared on success, so the lookup finds no row.
    async with session_factory() as session:
        with pytest.raises(TokenInvalidError):
            await verification_service.verify_token(plaintext, session)
