"""End-to-end flow test for SHU-508 Phase C.

Exercises the full path: `EmailService.send` → audit row written → queue
job enqueued → `process_email_queue_now()` drains the queue → worker
handler invokes `ConsoleEmailBackend` → audit row updated to `sent` with
the backend's `provider_message_id`.

Uses SQLite in-memory rather than Postgres — the test cares about ORM
behaviour, queue/handler integration, and audit row state transitions,
not Postgres-specific features (the partial idempotency index falls back
to a plain unique index on SQLite, which is fine because this test does
not exercise idempotency conflicts).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shu.core.cache_backend import InMemoryCacheBackend
from shu.core.email import ConsoleEmailBackend
from shu.core.queue_backend import InMemoryQueueBackend
from shu.models.email_send_log import EmailSendLog
from shu.services.email_service import EmailService


@pytest_asyncio.fixture
async def engine():
    """SQLite in-memory async engine with the email_send_log table created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        # Create only the email_send_log table — pulling in all models via
        # Base.metadata.create_all surfaces unrelated dialect issues
        # (Postgres-specific column types in other models).
        await conn.run_sync(
            lambda sync_conn: EmailSendLog.__table__.create(sync_conn, checkfirst=True)
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
    (root / "verify_email" / "subject.txt").write_text("Welcome, {{ name }}")
    (root / "verify_email" / "body.txt").write_text(
        "{% extends '_base.txt' %}{% block content %}Hi {{ name }}.{% endblock %}"
    )
    return root


@pytest.fixture
def queue() -> InMemoryQueueBackend:
    return InMemoryQueueBackend()


@pytest.fixture
def cache() -> InMemoryCacheBackend:
    return InMemoryCacheBackend()


@pytest.fixture
def service(
    templates_dir: Path,
    queue: InMemoryQueueBackend,
    cache: InMemoryCacheBackend,
) -> EmailService:
    return EmailService(
        queue=queue,
        cache=cache,
        from_address="noreply@example.com",
        from_name="Shu",
        templates_root=templates_dir,
    )


@pytest.mark.asyncio
async def test_full_send_to_audit_sent_flow(
    service: EmailService,
    queue: InMemoryQueueBackend,
    session_factory,
) -> None:
    """End-to-end: enqueue → drain → audit row reflects the dispatched send."""
    # Enqueue
    async with session_factory() as session:
        audit_id = await service.send(
            db=session,
            template_name="verify_email",
            to="user@example.com",
            context={"name": "Alice"},
        )
        await session.commit()

    # Verify queued state
    async with session_factory() as session:
        row = (
            await session.execute(select(EmailSendLog).where(EmailSendLog.id == audit_id))
        ).scalar_one()
        assert row.status == "queued"
        assert row.backend_name == "pending"
        assert row.provider_message_id is None
        assert row.sent_at is None

    # Drain — the worker handler runs against the same session_factory.
    # We patch get_async_session_local + get_email_backend so the handler
    # uses our test-controlled instances rather than the real config-driven
    # backend.
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

    # Verify sent state
    async with session_factory() as session:
        row = (
            await session.execute(select(EmailSendLog).where(EmailSendLog.id == audit_id))
        ).scalar_one()
        assert row.status == "sent"
        assert row.backend_name == "console"
        assert row.provider_message_id is not None
        assert row.provider_message_id.startswith("console-")
        assert row.sent_at is not None
