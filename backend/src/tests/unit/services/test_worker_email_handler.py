"""Unit tests for the EMAIL workload handler in `shu/worker.py`.

Covers the worker-side behaviour: building EmailMessage from payload,
calling the configured backend, audit row updates on success/failure,
and the transient-vs-permanent failure split that drives queue retry
semantics. EmailService-side behaviour is exercised by
`test_email_service.py`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.email.backend import (
    EmailMessage,
    EmailTransportError,
    SendResult,
    SendStatus,
)
from shu.core.queue_backend import Job
from shu.core.workload_routing import WorkloadType
from shu.email_handler import handle_email_job
from shu.services.email_service import build_message_from_payload


def _make_payload(audit_id: str = "audit-123") -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "template_name": "verify_email",
        "to": "user@example.com",
        "from_address": "noreply@example.com",
        "from_name": "Shu",
        "subject": "Welcome",
        "body_text": "Hi",
        "body_html": "<p>Hi</p>",
        "idempotency_key": None,
    }


def _make_job(payload: dict[str, Any] | None = None) -> Job:
    return Job(
        queue_name=WorkloadType.EMAIL.queue_name,
        payload=payload or _make_payload(),
    )


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_session_context(mock_session: AsyncMock):
    """Patch `get_async_session_local` to return a sessionmaker-shaped factory.

    `handle_email_job` calls `session_local() as session`. The outer
    `session_local()` must produce a fresh async context manager on each
    call (matching SQLAlchemy's `async_sessionmaker` shape), and the
    context yields the mock session.
    """

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    # session_local() — when invoked — returns a context manager
    session_local = MagicMock(side_effect=lambda: _ctx())
    # get_async_session_local() returns the sessionmaker
    factory = MagicMock(return_value=session_local)
    return factory, mock_session


# ---------------------------------------------------------------------------
# build_message_from_payload — payload contract
# ---------------------------------------------------------------------------


class TestBuildMessageFromPayload:
    def test_with_from_name_combines_into_rfc5322_form(self) -> None:
        msg = build_message_from_payload(_make_payload())
        assert isinstance(msg, EmailMessage)
        assert msg.from_address == "Shu <noreply@example.com>"
        assert msg.to == "user@example.com"
        assert msg.subject == "Welcome"
        assert msg.body_text == "Hi"
        assert msg.body_html == "<p>Hi</p>"

    def test_without_from_name_uses_bare_address(self) -> None:
        payload = _make_payload()
        payload["from_name"] = None
        msg = build_message_from_payload(payload)
        assert msg.from_address == "noreply@example.com"

    def test_missing_body_html_is_none(self) -> None:
        payload = _make_payload()
        del payload["body_html"]
        msg = build_message_from_payload(payload)
        assert msg.body_html is None


# ---------------------------------------------------------------------------
# handle_email_job — happy path and failure modes
# ---------------------------------------------------------------------------


class TestHandleEmailJob:
    @pytest.mark.asyncio
    async def test_success_marks_audit_sent_and_commits(self, mock_session_context) -> None:
        factory, session = mock_session_context

        backend = MagicMock()
        backend.name = "console"
        backend.send_email = AsyncMock(
            return_value=SendResult(
                status=SendStatus.SENT,
                backend_name="console",
                provider_message_id="console-abc",
            )
        )

        with (
            patch("shu.email_handler.get_email_backend", new=AsyncMock(return_value=backend)),
            patch("shu.email_handler.get_async_session_local", new=factory),
        ):
            await handle_email_job(_make_job())

        backend.send_email.assert_awaited_once()
        # session.execute called once for the UPDATE; session.commit called once
        assert session.execute.await_count == 1
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_permanent_failure_marks_audit_failed_and_does_not_raise(
        self, mock_session_context
    ) -> None:
        factory, session = mock_session_context

        backend = MagicMock()
        backend.name = "resend"
        backend.send_email = AsyncMock(
            return_value=SendResult(
                status=SendStatus.FAILED,
                backend_name="resend",
                error_message="422 invalid recipient",
            )
        )

        # Permanent failure must NOT raise (queue acks instead of retrying)
        with (
            patch("shu.email_handler.get_email_backend", new=AsyncMock(return_value=backend)),
            patch("shu.email_handler.get_async_session_local", new=factory),
        ):
            await handle_email_job(_make_job())

        # Audit row updated to failed
        assert session.execute.await_count == 1
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_transient_transport_error_propagates_for_queue_retry(
        self, mock_session_context
    ) -> None:
        factory, session = mock_session_context

        backend = MagicMock()
        backend.name = "smtp"
        backend.send_email = AsyncMock(
            side_effect=EmailTransportError("connection refused")
        )

        # Mid-retry: attempts < max_attempts so the queue will requeue.
        # Audit row must stay `queued` (no DB writes from this attempt).
        job = _make_job()
        job.attempts = 1
        job.max_attempts = 3

        with (  # noqa: SIM117 — ergonomic two-context patching with a third pytest.raises
            patch("shu.email_handler.get_email_backend", new=AsyncMock(return_value=backend)),
            patch("shu.email_handler.get_async_session_local", new=factory),
        ):
            with pytest.raises(EmailTransportError):
                await handle_email_job(job)

        session.execute.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminal_transport_error_marks_audit_failed_then_raises(
        self, mock_session_context
    ) -> None:
        """On the final retry, the handler must write the audit row failed BEFORE re-raising.

        Otherwise the queue gives up (job.attempts >= max_attempts → reject
        without requeue) and the audit row would be stuck in `queued`
        forever — which contradicts SHU-508's "max retries exceeded →
        marked failed" acceptance criterion.
        """
        factory, session = mock_session_context

        backend = MagicMock()
        backend.name = "smtp"
        backend.send_email = AsyncMock(
            side_effect=EmailTransportError("connection refused for the third time")
        )

        # Final attempt: attempts == max_attempts, queue will not requeue
        job = _make_job()
        job.attempts = 3
        job.max_attempts = 3

        with (  # noqa: SIM117
            patch("shu.email_handler.get_email_backend", new=AsyncMock(return_value=backend)),
            patch("shu.email_handler.get_async_session_local", new=factory),
        ):
            with pytest.raises(EmailTransportError):
                await handle_email_job(job)

        # Audit row marked failed before the exception propagated
        assert session.execute.await_count == 1
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_audit_id_raises_value_error(self) -> None:
        bad_job = Job(queue_name=WorkloadType.EMAIL.queue_name, payload={"to": "u@example.com"})
        with pytest.raises(ValueError, match="audit_id"):
            await handle_email_job(bad_job)
