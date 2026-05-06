"""Disabled email backend — accepts and drops all messages.

Used as the fallback when no email backend is configured or when required
configuration for the selected backend is missing. Returning success keeps
calling code from special-casing the "no email" deployment mode; flows that
cannot proceed without a real send (e.g. password reset) should detect the
disabled backend explicitly via `backend.name == "disabled"`.
"""

from .backend import EmailMessage, SendResult, SendStatus


class DisabledEmailBackend:
    """No-op email backend that returns success without sending."""

    @property
    def name(self) -> str:
        return "disabled"

    async def send_email(self, message: EmailMessage) -> SendResult:
        return SendResult(status=SendStatus.SENT, backend_name=self.name)
