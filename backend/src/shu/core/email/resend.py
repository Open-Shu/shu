"""Resend email backend.

Uses Resend's HTTP API via `httpx.AsyncClient`. Resend returns a JSON `id`
field on success, captured as `provider_message_id`. Their rate limit is
~10 req/s by default; transient 429/5xx responses raise
`EmailTransportError` so the queue worker handles backoff. 4xx responses
other than 429 return `status=FAILED` because they indicate a permanent
problem with the request itself (invalid address, missing API key) that
retries cannot fix.

API reference: https://resend.com/docs/api-reference/emails/send-email
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

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

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_TIMEOUT_SECONDS = 10.0


class ResendEmailBackend:
    """Send email via the Resend HTTP API."""

    def __init__(self, api_key: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> ResendEmailBackend:
        missing: list[str] = []
        if not settings.resend_api_key:
            missing.append("SHU_RESEND_API_KEY")
        if not settings.email_from_address:
            missing.append("SHU_EMAIL_FROM_ADDRESS")
        if missing:
            raise EmailConfigurationError(
                "Resend backend missing required configuration",
                details={"missing": missing},
            )

        return cls(api_key=settings.resend_api_key)  # type: ignore[arg-type]

    @property
    def name(self) -> str:
        return "resend"

    def _build_payload(self, message: EmailMessage) -> dict[str, object]:
        from_value = message.from_address
        payload: dict[str, object] = {
            "from": from_value,
            "to": [message.to],
            "subject": message.subject,
            "text": message.body_text,
        }
        if message.body_html:
            payload["html"] = message.body_html
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        if message.headers:
            payload["headers"] = dict(message.headers)
        return payload

    async def send_email(self, message: EmailMessage) -> SendResult:
        payload = self._build_payload(message)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # Resend supports `Idempotency-Key` for end-to-end de-duplication. We
        # forward it when the caller (EmailService) supplied one, so that if
        # the provider call succeeds but the post-send DB UPDATE fails and
        # the queue retries, Resend recognises the duplicate and returns the
        # original message-id instead of sending again.
        # https://resend.com/docs/api-reference/emails/send-email
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(RESEND_API_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise EmailTransportError(
                f"Resend API timeout after {self._timeout_seconds}s",
                details={"error": str(exc)},
            ) from exc
        except httpx.RequestError as exc:
            raise EmailTransportError(
                f"Resend API request failure: {exc}",
                details={"error": str(exc)},
            ) from exc

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = {}
            return SendResult(
                status=SendStatus.SENT,
                backend_name=self.name,
                provider_message_id=data.get("id"),
            )

        # 429 = rate limited, 5xx = server-side. Both are transient.
        if response.status_code == 429 or response.status_code >= 500:
            raise EmailTransportError(
                f"Resend API transient error: {response.status_code} {response.text[:200]}",
                details={
                    "status_code": response.status_code,
                    "body": response.text[:500],
                },
            )

        # Other 4xx — permanent. Record FAILED so audit captures the reason.
        logger.warning(
            "Resend API permanent failure",
            extra={
                "status_code": response.status_code,
                "body": response.text[:500],
                "to": message.to,
            },
        )
        return SendResult(
            status=SendStatus.FAILED,
            backend_name=self.name,
            error_message=f"{response.status_code} {response.text[:200]}",
        )
