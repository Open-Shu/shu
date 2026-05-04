"""SMTP email backend for self-hosted deployments.

Uses `aiosmtplib` for async send. Supports STARTTLS (port 587, default),
implicit TLS (port 465), and plaintext (no TLS — dev only). Returns a
`SendResult` with the local Message-Id header as `provider_message_id` when
the SMTP server accepts the message.

Permanent failures (5xx replies, recipient rejected) return
`status=FAILED` so the audit log captures the outcome. Transient failures
(connection refused, timeout) raise `EmailTransportError` so the queue
worker retries with backoff.
"""

from __future__ import annotations

from email.message import EmailMessage as MIMEEmailMessage
from email.utils import make_msgid
from typing import TYPE_CHECKING

import aiosmtplib

from ..logging import get_logger
from .backend import (
    EmailConfigurationError,
    EmailMessage,
    EmailTransportError,
    SendResult,
    SendStatus,
)

if TYPE_CHECKING:
    from ..config import Settings

logger = get_logger(__name__)


class SMTPEmailBackend:
    """Send email via SMTP using aiosmtplib."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        user: str | None,
        password: str | None,
        tls_mode: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_tls = tls_mode == "tls"
        self._start_tls = tls_mode == "starttls"

    @classmethod
    def from_settings(cls, settings: Settings) -> SMTPEmailBackend:
        missing: list[str] = []
        if not settings.smtp_host:
            missing.append("SHU_SMTP_HOST")
        if not settings.email_from_address:
            missing.append("SHU_EMAIL_FROM_ADDRESS")
        if missing:
            raise EmailConfigurationError(
                "SMTP backend missing required configuration",
                details={"missing": missing},
            )

        return cls(
            host=settings.smtp_host,  # type: ignore[arg-type]
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            tls_mode=(settings.smtp_tls_mode or "starttls").strip().lower(),
        )

    @property
    def name(self) -> str:
        return "smtp"

    def _build_mime(self, message: EmailMessage) -> tuple[MIMEEmailMessage, str]:
        mime = MIMEEmailMessage()
        mime["From"] = message.from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        if message.reply_to:
            mime["Reply-To"] = message.reply_to

        message_id = make_msgid()
        mime["Message-Id"] = message_id

        for header, value in (message.headers or {}).items():
            mime[header] = value

        mime.set_content(message.body_text)
        if message.body_html:
            mime.add_alternative(message.body_html, subtype="html")

        return mime, message_id

    async def send_email(self, message: EmailMessage) -> SendResult:
        mime, message_id = self._build_mime(message)

        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                use_tls=self._use_tls,
                start_tls=self._start_tls,
            )
        except aiosmtplib.SMTPResponseException as exc:
            # 5xx codes are permanent rejections (bad recipient, message
            # refused). Record as FAILED in the audit log instead of raising
            # so the queue does not waste retries on a known-bad message.
            if 500 <= exc.code < 600:
                logger.warning(
                    "SMTP permanent failure",
                    extra={"code": exc.code, "smtp_message": exc.message, "to": message.to},
                )
                return SendResult(
                    status=SendStatus.FAILED,
                    backend_name=self.name,
                    error_message=f"{exc.code} {exc.message}",
                )
            raise EmailTransportError(
                f"SMTP transient error: {exc.code} {exc.message}",
                details={"code": exc.code, "message": exc.message},
            ) from exc
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise EmailTransportError(
                f"SMTP transport failure: {exc}",
                details={"error": str(exc)},
            ) from exc

        return SendResult(
            status=SendStatus.SENT,
            backend_name=self.name,
            provider_message_id=message_id,
        )
