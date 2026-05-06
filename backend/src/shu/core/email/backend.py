"""EmailBackend protocol, message dataclasses, and exception hierarchy.

Designed to be wire-format-compatible with the control-plane mirror of this
module (SHU-746). Field names and types must stay aligned with
`shu-control-plane/src/control_plane/email/backend.py` so that the
`ControlPlaneEmailBackend` (SHU-749) can serialise an `EmailMessage` over the
relay endpoint without translation.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class EmailBackendError(Exception):
    """Base exception for email backend operations.

    Attributes:
        message: Human-readable error description.
        details: Optional dictionary with additional error context.

    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class EmailConfigurationError(EmailBackendError):
    """Raised when an email backend cannot be constructed due to missing or invalid configuration.

    Distinct from `EmailTransportError` because configuration problems are
    operator-fixable at deploy time, while transport failures are typically
    transient runtime conditions.
    """

    pass


class EmailTransportError(EmailBackendError):
    """Raised when sending fails at the transport layer (SMTP, HTTP, etc.).

    Consumers should treat this as transient unless `permanent=True` is set in
    `details`, which signals the provider rejected the message permanently
    (e.g. invalid recipient, account suspended).
    """

    pass


class SendStatus(str, Enum):
    """Outcome of a send attempt as recorded in `email_send_log`."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


@dataclass
class EmailMessage:
    """A single outbound email message.

    The shape is intentionally minimal and explicit. Fields kept close to RFC
    5322 / Resend / aiosmtplib semantics so backends do not need to translate.

    Attributes:
        to: Primary recipient address. Multi-recipient sends are out of scope
            for MVP — fan out at the service layer if needed.
        subject: Message subject. Backends are responsible for any encoding
            required for non-ASCII characters.
        body_text: Plain-text body. Required (used as the fallback when an
            HTML body is also provided).
        from_address: Sender address. Validation is the caller's job.
        body_html: Optional HTML body. When present, sent alongside `body_text`
            as a multipart/alternative message.
        reply_to: Optional Reply-To address.
        headers: Optional dict of additional headers. Header names should be
            lowercased; backends pass them through with backend-specific
            allowlisting where applicable.
        idempotency_key: Optional caller-supplied key for end-to-end
            de-duplication. Backends that support provider-side idempotency
            (Resend, the control-plane relay) forward this so a retry after
            a partial failure does not produce a duplicate send. Backends
            without provider de-dup (SMTP, Console, Disabled) ignore it.

    """

    to: str
    subject: str
    body_text: str
    from_address: str
    body_html: str | None = None
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass
class SendResult:
    """Outcome of a single `send_email` call.

    Attributes:
        status: Final status of the attempt.
        backend_name: Identifier of the backend that handled the send (e.g.
            "smtp", "resend", "console", "disabled", "control_plane"). Used in
            audit logs.
        provider_message_id: Provider-supplied id for the dispatched message,
            when available. SMTP returns the local message id; Resend returns
            their `id`; console/disabled return None.
        error_message: Populated when status is `FAILED`. None otherwise.

    """

    status: SendStatus
    backend_name: str
    provider_message_id: str | None = None
    error_message: str | None = None


@runtime_checkable
class EmailBackend(Protocol):
    """Protocol defining the email backend interface.

    Implementations must be safe to call from multiple coroutines concurrently.
    They should not retain per-message state between calls — `EmailService`
    owns retry, queueing, and audit, not the backend.

    Implementations must NOT raise on permanent send failures (e.g. provider
    rejected an invalid address); instead return a `SendResult` with
    `status=FAILED` so the audit trail captures the outcome. Raise
    `EmailTransportError` only for transient conditions worth retrying
    (network timeouts, 5xx responses).
    """

    @property
    def name(self) -> str:
        """Stable identifier for this backend (e.g. "smtp", "resend").

        Used as the `backend_name` field on `SendResult` and recorded in the
        `email_send_log` audit table.
        """
        ...

    async def send_email(self, message: EmailMessage) -> SendResult:
        """Send a single email message.

        Args:
            message: The message to deliver.

        Returns:
            A `SendResult` describing the outcome. `status=SENT` indicates
            the provider accepted the message; it does not guarantee delivery
            to the recipient inbox.

        Raises:
            EmailTransportError: Transient transport failure worth retrying.

        """
        ...
