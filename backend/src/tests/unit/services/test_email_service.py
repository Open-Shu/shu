"""Unit tests for EmailService (SHU-508 Phase C).

Covers the mid-layer behaviour: template rendering, audit row writes,
queue enqueueing, idempotency dedup, and the rate-limit helper. Worker
handler behaviour (queue dispatch, audit row updates after send) is
exercised by `test_worker_email_handler.py`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from shu.core.cache_backend import InMemoryCacheBackend
from shu.core.queue_backend import InMemoryQueueBackend
from shu.core.workload_routing import WorkloadType
from shu.services.email_service import (
    EmailService,
    EmailServiceError,
    TemplateNotFoundError,
)


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a `verify_email` template the EmailService can render."""
    root = tmp_path / "templates" / "email"
    (root / "verify_email").mkdir(parents=True)

    (root / "_base.html").write_text(
        "<html><body>{% block content %}{% endblock %}</body></html>"
    )
    (root / "_base.txt").write_text(
        "{%- block content %}{% endblock -%}\n"
    )
    (root / "verify_email" / "subject.txt").write_text(
        "Welcome, {{ name }}"
    )
    (root / "verify_email" / "body.txt").write_text(
        "{% extends '_base.txt' %}{% block content %}Hi {{ name }}, click {{ link }}.{% endblock %}"
    )
    (root / "verify_email" / "body.html").write_text(
        "{% extends '_base.html' %}{% block content %}<p>Hi {{ name }}, click <a href=\"{{ link }}\">{{ link }}</a>.</p>{% endblock %}"
    )
    return root


@pytest.fixture
def queue() -> InMemoryQueueBackend:
    return InMemoryQueueBackend()


@pytest.fixture
def cache() -> InMemoryCacheBackend:
    return InMemoryCacheBackend()


@pytest.fixture
def mock_db() -> AsyncMock:
    """AsyncSession that records adds and returns no existing rows by default."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    # AsyncSession.begin_nested() returns an async context manager
    # (AsyncSessionTransaction). Stub one whose body raises if needed
    # in specific tests; default success path just no-ops on enter/exit.
    nested_ctx = AsyncMock()
    nested_ctx.__aenter__ = AsyncMock(return_value=nested_ctx)
    nested_ctx.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_ctx)

    # Default: no existing idempotency row
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    return db


@pytest.fixture
def service(templates_dir: Path, queue: InMemoryQueueBackend, cache: InMemoryCacheBackend) -> EmailService:
    return EmailService(
        queue=queue,
        cache=cache,
        from_address="noreply@example.com",
        from_name="Shu Test",
        templates_root=templates_dir,
        app_name="ShuTest",
        support_email="support@example.com",
    )


# ---------------------------------------------------------------------------
# send() — happy path
# ---------------------------------------------------------------------------


class TestSendHappyPath:
    @pytest.mark.asyncio
    async def test_send_writes_audit_row_and_enqueues_job(
        self,
        service: EmailService,
        mock_db: AsyncMock,
        queue: InMemoryQueueBackend,
    ) -> None:
        audit_id = await service.send(
            db=mock_db,
            template_name="verify_email",
            to="user@example.com",
            context={"name": "Alice", "link": "https://shu.example/v?t=abc"},
        )

        # Audit row added with the right shape and queued status
        assert mock_db.add.call_count == 1
        added_row = mock_db.add.call_args.args[0]
        assert added_row.id == audit_id
        assert added_row.to_address == "user@example.com"
        assert added_row.template_name == "verify_email"
        assert added_row.status == "queued"
        assert added_row.idempotency_key is None
        assert mock_db.flush.await_count == 1

        # Queue job enqueued with the rendered payload
        pending = await queue.peek(WorkloadType.EMAIL.queue_name, limit=10)
        assert len(pending) == 1
        payload = pending[0].payload
        assert payload["audit_id"] == audit_id
        assert payload["to"] == "user@example.com"
        assert payload["template_name"] == "verify_email"
        assert payload["from_address"] == "noreply@example.com"
        assert payload["from_name"] == "Shu Test"
        assert payload["subject"] == "Welcome, Alice"
        assert "Hi Alice" in payload["body_text"]
        assert "https://shu.example/v?t=abc" in payload["body_text"]
        assert payload["body_html"] is not None
        assert "<a href=" in payload["body_html"]

    @pytest.mark.asyncio
    async def test_html_renders_with_autoescape_text_does_not(
        self,
        service: EmailService,
        mock_db: AsyncMock,
        queue: InMemoryQueueBackend,
    ) -> None:
        await service.send(
            db=mock_db,
            template_name="verify_email",
            to="user@example.com",
            context={"name": "<script>", "link": "https://shu.example/v?a=1&b=2"},
        )

        payload = (await queue.peek(WorkloadType.EMAIL.queue_name, limit=1))[0].payload

        # HTML escapes — "<script>" must appear escaped, "&" in URL too
        assert "<script>" not in payload["body_html"]
        assert "&lt;script&gt;" in payload["body_html"]

        # Plain text does NOT escape — &, <, > must appear verbatim
        assert "<script>" in payload["body_text"]
        assert "&lt;" not in payload["body_text"]
        assert "a=1&b=2" in payload["body_text"]

    @pytest.mark.asyncio
    async def test_subject_is_stripped_of_trailing_whitespace(
        self,
        templates_dir: Path,
        queue: InMemoryQueueBackend,
        cache: InMemoryCacheBackend,
        mock_db: AsyncMock,
    ) -> None:
        # Jinja2 templates often render with a trailing newline; the service
        # strips subject whitespace so headers are clean.
        (templates_dir / "verify_email" / "subject.txt").write_text("Welcome, {{ name }}\n\n")

        service = EmailService(
            queue=queue,
            cache=cache,
            from_address="noreply@example.com",
            templates_root=templates_dir,
        )
        await service.send(
            db=mock_db, template_name="verify_email", to="u@example.com", context={"name": "A", "link": "x"}
        )
        payload = (await queue.peek(WorkloadType.EMAIL.queue_name, limit=1))[0].payload
        assert payload["subject"] == "Welcome, A"


# ---------------------------------------------------------------------------
# send() — configuration and template errors
# ---------------------------------------------------------------------------


class TestSendErrors:
    @pytest.mark.asyncio
    async def test_missing_from_address_raises(
        self,
        templates_dir: Path,
        queue: InMemoryQueueBackend,
        cache: InMemoryCacheBackend,
        mock_db: AsyncMock,
    ) -> None:
        service = EmailService(
            queue=queue, cache=cache, from_address=None, templates_root=templates_dir
        )
        with pytest.raises(EmailServiceError, match="SHU_EMAIL_FROM_ADDRESS"):
            await service.send(db=mock_db, template_name="verify_email", to="u@example.com", context={})

    @pytest.mark.asyncio
    async def test_unknown_template_raises_template_not_found(
        self, service: EmailService, mock_db: AsyncMock
    ) -> None:
        with pytest.raises(TemplateNotFoundError):
            await service.send(db=mock_db, template_name="does_not_exist", to="u@example.com", context={})

    @pytest.mark.asyncio
    async def test_html_optional_template_missing_is_fine(
        self,
        templates_dir: Path,
        queue: InMemoryQueueBackend,
        cache: InMemoryCacheBackend,
        mock_db: AsyncMock,
    ) -> None:
        # Remove body.html — text-only sends are valid
        (templates_dir / "verify_email" / "body.html").unlink()
        service = EmailService(
            queue=queue, cache=cache, from_address="noreply@example.com", templates_root=templates_dir
        )
        await service.send(
            db=mock_db, template_name="verify_email", to="u@example.com", context={"name": "A", "link": "x"}
        )
        payload = (await queue.peek(WorkloadType.EMAIL.queue_name, limit=1))[0].payload
        assert payload["body_html"] is None


# ---------------------------------------------------------------------------
# send() — idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_returns_existing_id_when_key_already_used(
        self, service: EmailService, mock_db: AsyncMock, queue: InMemoryQueueBackend
    ) -> None:
        existing_id = "audit-already-here"

        # First _find_existing call (the dedup check) returns existing_id
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = existing_id
        mock_db.execute = AsyncMock(return_value=existing_result)

        returned = await service.send(
            db=mock_db,
            template_name="verify_email",
            to="user@example.com",
            context={"name": "A", "link": "x"},
            idempotency_key="signup-attempt-42",
        )

        assert returned == existing_id
        mock_db.add.assert_not_called()
        # Nothing enqueued — the original audit row's worker run handles it
        pending = await queue.peek(WorkloadType.EMAIL.queue_name, limit=10)
        assert pending == []

    @pytest.mark.asyncio
    async def test_race_condition_on_unique_constraint_returns_winner(
        self, service: EmailService, mock_db: AsyncMock, queue: InMemoryQueueBackend
    ) -> None:
        # Simulate: two concurrent sends, both pass the initial dedup check
        # (no row yet), one wins the INSERT, the other hits the unique
        # constraint and must surface the winner's id.
        winner_id = "audit-winner"

        miss = MagicMock()
        miss.scalar_one_or_none.return_value = None
        winner = MagicMock()
        winner.scalar_one_or_none.return_value = winner_id
        # First execute (initial dedup): miss. Second (post-savepoint-rollback
        # lookup): winner.
        mock_db.execute = AsyncMock(side_effect=[miss, winner])
        mock_db.flush = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("dup")))

        returned = await service.send(
            db=mock_db,
            template_name="verify_email",
            to="user@example.com",
            context={"name": "A", "link": "x"},
            idempotency_key="signup-attempt-42",
        )

        assert returned == winner_id
        # The savepoint (begin_nested) rolls back automatically on
        # IntegrityError; the OUTER session.rollback() must NOT be
        # called — that would wipe unrelated caller mutations (user row,
        # token row, etc.).
        mock_db.rollback.assert_not_awaited()
        mock_db.begin_nested.assert_called_once()
        # Original send's job was never enqueued because flush failed
        pending = await queue.peek(WorkloadType.EMAIL.queue_name, limit=10)
        assert pending == []


# ---------------------------------------------------------------------------
# check_rate_limit
# ---------------------------------------------------------------------------


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_under_limit_returns_true_and_consumes_slot(
        self, service: EmailService, cache: InMemoryCacheBackend
    ) -> None:
        for i in range(3):
            assert await service.check_rate_limit(
                template_name="verify_email",
                to="user@example.com",
                max_per_window=3,
                window_seconds=3600,
            ) is True, f"call {i + 1} should have been allowed"

    @pytest.mark.asyncio
    async def test_exceeding_limit_returns_false(
        self, service: EmailService, cache: InMemoryCacheBackend
    ) -> None:
        # Consume the limit
        for _ in range(3):
            await service.check_rate_limit(
                template_name="verify_email",
                to="user@example.com",
                max_per_window=3,
                window_seconds=3600,
            )
        # Fourth call should be blocked
        assert await service.check_rate_limit(
            template_name="verify_email",
            to="user@example.com",
            max_per_window=3,
            window_seconds=3600,
        ) is False

    @pytest.mark.asyncio
    async def test_separate_addresses_have_separate_buckets(
        self, service: EmailService
    ) -> None:
        for _ in range(3):
            await service.check_rate_limit(
                template_name="verify_email",
                to="user1@example.com",
                max_per_window=3,
                window_seconds=3600,
            )
        # user2 still has a fresh bucket
        assert await service.check_rate_limit(
            template_name="verify_email",
            to="user2@example.com",
            max_per_window=3,
            window_seconds=3600,
        ) is True

    @pytest.mark.asyncio
    async def test_separate_templates_have_separate_buckets(
        self, service: EmailService
    ) -> None:
        for _ in range(3):
            await service.check_rate_limit(
                template_name="verify_email",
                to="user@example.com",
                max_per_window=3,
                window_seconds=3600,
            )
        # password_reset for the same address is a different bucket
        assert await service.check_rate_limit(
            template_name="password_reset",
            to="user@example.com",
            max_per_window=3,
            window_seconds=3600,
        ) is True
