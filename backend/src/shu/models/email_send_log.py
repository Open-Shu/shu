"""Audit log for outbound email sends (SHU-508).

One row per `EmailService.send` call. Written at enqueue time with
`status=queued`, updated by the EMAIL workload-type worker handler to
`sent` (with `provider_message_id`) or `failed` (with `error_message`).
The queued state is observable in the audit table without dequeueing,
which is the only DB-visible signal that a send is in flight.

The unique partial index on `(template_name, to_address, idempotency_key)`
where `idempotency_key IS NOT NULL` enforces idempotency at the DB level —
a retried `EmailService.send` with the same key returns the existing row
rather than enqueueing a duplicate job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Column, Index, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from shu.core.database import Base


class EmailSendLog(Base):
    """Audit row for one outbound email send."""

    __tablename__ = "email_send_log"

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'sent', 'failed')",
            name="email_send_log_status_check",
        ),
        # Support / debugging: "show all sends to address X, newest first"
        Index(
            "ix_email_send_log_to_address_created_at",
            "to_address",
            "created_at",
            postgresql_using="btree",
        ),
        # Idempotency: at most one row per (template, recipient, key) when
        # the caller passes a key. NULL keys are allowed to repeat.
        Index(
            "uq_email_send_log_idempotency",
            "template_name",
            "to_address",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    to_address = Column(Text, nullable=False)
    template_name = Column(Text, nullable=False)
    backend_name = Column(Text, nullable=False)
    provider_message_id = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="queued")
    error_message = Column(Text, nullable=True)
    idempotency_key = Column(Text, nullable=True)

    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    sent_at = Column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"<EmailSendLog(id={self.id!r}, to={self.to_address!r}, "
            f"template={self.template_name!r}, status={self.status!r})>"
        )
