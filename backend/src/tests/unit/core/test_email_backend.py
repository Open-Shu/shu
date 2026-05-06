"""Unit tests for the email backend protocol and standalone implementations.

Covers the foundation backends delivered by SHU-508 Phase A/B:
- `DisabledEmailBackend` (no-op)
- `ConsoleEmailBackend` (logs)
- `SMTPEmailBackend` (aiosmtplib)
- `ResendEmailBackend` (httpx)
- factory selection + missing-config fallback

Templates, queueing, audit, and rate limiting belong to the `EmailService`
mid-layer (Phase C) and are not exercised here.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from shu.core.email import (
    ConsoleEmailBackend,
    DisabledEmailBackend,
    EmailMessage,
    SendStatus,
    get_email_backend,
    reset_email_backend,
)
from shu.core.email.backend import EmailConfigurationError, EmailTransportError
from shu.core.email.factory import _build_backend
from shu.core.email.resend import ResendEmailBackend
from shu.core.email.smtp import SMTPEmailBackend


@pytest.fixture
def message() -> EmailMessage:
    return EmailMessage(
        to="recipient@example.com",
        subject="Test",
        body_text="Hello",
        body_html="<p>Hello</p>",
        from_address="noreply@example.com",
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_email_backend()
    yield
    reset_email_backend()


# ---------------------------------------------------------------------------
# DisabledEmailBackend
# ---------------------------------------------------------------------------


class TestDisabledBackend:
    @pytest.mark.asyncio
    async def test_returns_sent_without_doing_anything(self, message: EmailMessage) -> None:
        backend = DisabledEmailBackend()
        result = await backend.send_email(message)
        assert result.status == SendStatus.SENT
        assert result.backend_name == "disabled"
        assert result.provider_message_id is None
        assert result.error_message is None

    def test_name(self) -> None:
        assert DisabledEmailBackend().name == "disabled"


# ---------------------------------------------------------------------------
# ConsoleEmailBackend
# ---------------------------------------------------------------------------


class TestConsoleBackend:
    @pytest.mark.asyncio
    async def test_logs_message_and_returns_sent(
        self, message: EmailMessage, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = ConsoleEmailBackend()
        with caplog.at_level(logging.INFO, logger="shu.core.email.console"):
            result = await backend.send_email(message)

        assert result.status == SendStatus.SENT
        assert result.backend_name == "console"
        assert result.provider_message_id is not None
        assert result.provider_message_id.startswith("console-")

        # Subject and recipient appear in the structured log payload.
        record = next(r for r in caplog.records if r.name == "shu.core.email.console")
        assert record.to == message.to
        assert record.subject == message.subject
        assert record.body_text == message.body_text


# ---------------------------------------------------------------------------
# SMTPEmailBackend
# ---------------------------------------------------------------------------


class TestSMTPBackend:
    @pytest.mark.asyncio
    async def test_send_success_returns_message_id(self, message: EmailMessage) -> None:
        backend = SMTPEmailBackend(
            host="smtp.example.com",
            port=587,
            user=None,
            password=None,
            tls_mode="starttls",
        )
        with patch("shu.core.email.smtp.aiosmtplib.send", new=AsyncMock(return_value=None)) as mock_send:
            result = await backend.send_email(message)

        assert result.status == SendStatus.SENT
        assert result.backend_name == "smtp"
        assert result.provider_message_id is not None
        # Message-Id should be RFC 5322 form: <something@host>
        assert result.provider_message_id.startswith("<") and result.provider_message_id.endswith(">")
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_5xx_response_returns_failed_not_raises(self, message: EmailMessage) -> None:
        import aiosmtplib

        backend = SMTPEmailBackend(
            host="smtp.example.com",
            port=587,
            user=None,
            password=None,
            tls_mode="starttls",
        )
        exc = aiosmtplib.SMTPResponseException(550, "Mailbox not found")
        with patch("shu.core.email.smtp.aiosmtplib.send", new=AsyncMock(side_effect=exc)):
            result = await backend.send_email(message)

        assert result.status == SendStatus.FAILED
        assert result.backend_name == "smtp"
        assert "550" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_4xx_response_raises_transport_error(self, message: EmailMessage) -> None:
        import aiosmtplib

        backend = SMTPEmailBackend(
            host="smtp.example.com",
            port=587,
            user=None,
            password=None,
            tls_mode="starttls",
        )
        exc = aiosmtplib.SMTPResponseException(421, "Service not available")
        with (
            patch("shu.core.email.smtp.aiosmtplib.send", new=AsyncMock(side_effect=exc)),
            pytest.raises(EmailTransportError),
        ):
            await backend.send_email(message)

    @pytest.mark.asyncio
    async def test_oserror_raises_transport_error(self, message: EmailMessage) -> None:
        backend = SMTPEmailBackend(
            host="smtp.example.com",
            port=587,
            user=None,
            password=None,
            tls_mode="starttls",
        )
        with (
            patch(
                "shu.core.email.smtp.aiosmtplib.send",
                new=AsyncMock(side_effect=ConnectionRefusedError("nope")),
            ),
            pytest.raises(EmailTransportError),
        ):
            await backend.send_email(message)

    def test_from_settings_missing_host_raises(self) -> None:
        from types import SimpleNamespace

        settings = SimpleNamespace(
            smtp_host=None,
            smtp_port=587,
            smtp_user=None,
            smtp_password=None,
            smtp_tls_mode="starttls",
            email_from_address="noreply@example.com",
        )
        with pytest.raises(EmailConfigurationError) as ei:
            SMTPEmailBackend.from_settings(settings)  # type: ignore[arg-type]
        assert "SHU_SMTP_HOST" in ei.value.details["missing"]


# ---------------------------------------------------------------------------
# ResendEmailBackend
# ---------------------------------------------------------------------------


class _MockAsyncClient:
    """Context-manager-compatible stub for httpx.AsyncClient used in tests.

    httpx.AsyncClient supports `async with`; the unittest mock plumbing for
    that is verbose. This is the minimum surface ResendEmailBackend uses.
    """

    def __init__(self, response: httpx.Response | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class TestResendBackend:
    @pytest.mark.asyncio
    async def test_send_success_returns_resend_id(self, message: EmailMessage) -> None:
        backend = ResendEmailBackend(api_key="re_test")
        response = httpx.Response(200, json={"id": "abc-123"})
        with patch("shu.core.email.resend.httpx.AsyncClient", return_value=_MockAsyncClient(response)):
            result = await backend.send_email(message)

        assert result.status == SendStatus.SENT
        assert result.backend_name == "resend"
        assert result.provider_message_id == "abc-123"

    @pytest.mark.asyncio
    async def test_idempotency_key_forwarded_as_header(self) -> None:
        """When EmailMessage carries an idempotency_key, the Resend backend must
        forward it as `Idempotency-Key`. Closes the duplicate-send window where
        a successful provider call followed by a DB UPDATE failure causes the
        queue to retry the job — Resend's de-dup recognises the key and does
        not send twice.
        """

        class _CapturingClient:
            captured_headers: ClassVar[dict[str, str]] = {}

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *_exc):  # type: ignore[no-untyped-def]
                return None

            async def post(self, _url, *, json, headers):  # type: ignore[no-untyped-def]
                _CapturingClient.captured_headers = headers
                return httpx.Response(200, json={"id": "msg-1"})

        backend = ResendEmailBackend(api_key="re_test")
        msg = EmailMessage(
            to="user@example.com",
            subject="Hi",
            body_text="Hello",
            from_address="noreply@example.com",
            idempotency_key="signup-attempt-42",
        )
        with patch("shu.core.email.resend.httpx.AsyncClient", new=_CapturingClient):
            result = await backend.send_email(msg)

        assert result.status == SendStatus.SENT
        assert _CapturingClient.captured_headers.get("Idempotency-Key") == "signup-attempt-42"

    @pytest.mark.asyncio
    async def test_no_idempotency_key_no_header(self, message: EmailMessage) -> None:
        """When no key is supplied, the header must NOT be sent (otherwise we
        would pin Resend's de-dup to an empty value and break legitimate repeats).
        """

        class _CapturingClient:
            captured_headers: ClassVar[dict[str, str]] = {}

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def __aenter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __aexit__(self, *_exc):  # type: ignore[no-untyped-def]
                return None

            async def post(self, _url, *, json, headers):  # type: ignore[no-untyped-def]
                _CapturingClient.captured_headers = headers
                return httpx.Response(200, json={"id": "msg-1"})

        backend = ResendEmailBackend(api_key="re_test")
        # `message` fixture has no idempotency_key
        with patch("shu.core.email.resend.httpx.AsyncClient", new=_CapturingClient):
            await backend.send_email(message)

        assert "Idempotency-Key" not in _CapturingClient.captured_headers

    @pytest.mark.asyncio
    async def test_429_raises_transport_error(self, message: EmailMessage) -> None:
        backend = ResendEmailBackend(api_key="re_test")
        response = httpx.Response(429, text="rate limited")
        with (
            patch("shu.core.email.resend.httpx.AsyncClient", return_value=_MockAsyncClient(response)),
            pytest.raises(EmailTransportError),
        ):
            await backend.send_email(message)

    @pytest.mark.asyncio
    async def test_5xx_raises_transport_error(self, message: EmailMessage) -> None:
        backend = ResendEmailBackend(api_key="re_test")
        response = httpx.Response(503, text="service unavailable")
        with (
            patch("shu.core.email.resend.httpx.AsyncClient", return_value=_MockAsyncClient(response)),
            pytest.raises(EmailTransportError),
        ):
            await backend.send_email(message)

    @pytest.mark.asyncio
    async def test_4xx_returns_failed(self, message: EmailMessage) -> None:
        backend = ResendEmailBackend(api_key="re_test")
        response = httpx.Response(422, text="invalid recipient")
        with patch("shu.core.email.resend.httpx.AsyncClient", return_value=_MockAsyncClient(response)):
            result = await backend.send_email(message)

        assert result.status == SendStatus.FAILED
        assert result.backend_name == "resend"
        assert "422" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_timeout_raises_transport_error(self, message: EmailMessage) -> None:
        backend = ResendEmailBackend(api_key="re_test")
        with (
            patch(
                "shu.core.email.resend.httpx.AsyncClient",
                return_value=_MockAsyncClient(httpx.TimeoutException("timeout")),
            ),
            pytest.raises(EmailTransportError),
        ):
            await backend.send_email(message)

    def test_from_settings_missing_key_raises(self) -> None:
        from types import SimpleNamespace

        settings = SimpleNamespace(
            resend_api_key=None,
            email_from_address="noreply@example.com",
        )
        with pytest.raises(EmailConfigurationError) as ei:
            ResendEmailBackend.from_settings(settings)  # type: ignore[arg-type]
        assert "SHU_RESEND_API_KEY" in ei.value.details["missing"]


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------


class TestFactorySelection:
    def _settings(self, **overrides: Any) -> Any:
        from types import SimpleNamespace

        defaults: dict[str, Any] = {
            "email_backend": "disabled",
            "email_from_address": None,
            "email_from_name": None,
            "smtp_host": None,
            "smtp_port": 587,
            "smtp_user": None,
            "smtp_password": None,
            "smtp_tls_mode": "starttls",
            "resend_api_key": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_disabled_default(self) -> None:
        with patch("shu.core.config.get_settings_instance", return_value=self._settings()):
            backend = _build_backend()
        assert isinstance(backend, DisabledEmailBackend)

    def test_console_selected(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="console"),
        ):
            backend = _build_backend()
        assert isinstance(backend, ConsoleEmailBackend)

    def test_smtp_with_full_config_selected(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(
                email_backend="smtp",
                smtp_host="smtp.example.com",
                email_from_address="noreply@example.com",
            ),
        ):
            backend = _build_backend()
        assert isinstance(backend, SMTPEmailBackend)

    def test_smtp_missing_config_falls_back_to_disabled(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="smtp"),
        ):
            backend = _build_backend()
        assert isinstance(backend, DisabledEmailBackend)

    def test_resend_with_full_config_selected(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(
                email_backend="resend",
                resend_api_key="re_test",
                email_from_address="noreply@example.com",
            ),
        ):
            backend = _build_backend()
        assert isinstance(backend, ResendEmailBackend)

    def test_resend_missing_key_falls_back_to_disabled(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="resend"),
        ):
            backend = _build_backend()
        assert isinstance(backend, DisabledEmailBackend)

    def test_unknown_value_falls_back_to_disabled(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="postmark"),
        ):
            backend = _build_backend()
        assert isinstance(backend, DisabledEmailBackend)

    def test_control_plane_unimplemented_falls_back_to_disabled(self) -> None:
        # SHU-749 will land ControlPlaneEmailBackend later. Until then, the
        # factory must degrade gracefully to disabled rather than crashing.
        # The backend will read SHU_CP_BASE_URL from billing/config.py
        # (introduced by SHU-743), not from core Settings.
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="control_plane"),
        ):
            backend = _build_backend()
        assert isinstance(backend, DisabledEmailBackend)

    @pytest.mark.asyncio
    async def test_get_email_backend_caches_singleton(self) -> None:
        with patch(
            "shu.core.config.get_settings_instance",
            return_value=self._settings(email_backend="console"),
        ):
            first = await get_email_backend()
            second = await get_email_backend()
        assert first is second
