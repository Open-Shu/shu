"""Console email backend — logs the message instead of sending.

For local development and CI. Logs at INFO with a structured payload so the
output is easy to assert on in integration tests. Body content is logged in
full because dev/CI is the only context where this backend runs; production
deployments use SMTP, Resend, or the control plane.
"""

import uuid

from ..logging import get_logger
from .backend import EmailMessage, SendResult, SendStatus

logger = get_logger(__name__)


class ConsoleEmailBackend:
    """Logs each outbound message and returns success."""

    @property
    def name(self) -> str:
        return "console"

    async def send_email(self, message: EmailMessage) -> SendResult:
        message_id = f"console-{uuid.uuid4()}"
        logger.info(
            "email.send (console backend)",
            extra={
                "event": "email.send",
                "backend": self.name,
                "to": message.to,
                "from": message.from_address,
                "subject": message.subject,
                "body_text": message.body_text,
                "body_html": message.body_html,
                "reply_to": message.reply_to,
                "headers": message.headers,
                "provider_message_id": message_id,
            },
        )
        return SendResult(
            status=SendStatus.SENT,
            backend_name=self.name,
            provider_message_id=message_id,
        )
