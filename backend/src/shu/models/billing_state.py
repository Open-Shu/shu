"""ORM models for typed billing state storage.

Replaces the untyped system_settings["billing"] JSON blob with a singleton
typed table plus an append-only audit log. Concurrent mutations use
row-level locking (SELECT ... FOR UPDATE) so no field update is silently
clobbered by a racing webhook handler.

Tables:
    billing_state       — singleton row (id = 1) with all billing fields
    billing_state_audit — append-only field-change log for diagnostics
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from shu.core.database import Base


class BillingState(Base):
    """Singleton billing state row (id is always 1).

    All webhook handlers and scheduler jobs MUST go through
    BillingStateService.update() to mutate this row — never write directly
    — so the row-level lock and audit trail are never bypassed.
    """

    __tablename__ = "billing_state"

    __table_args__ = (
        CheckConstraint("id = 1", name="billing_state_singleton"),
        CheckConstraint(
            "user_limit_enforcement IN ('soft', 'hard', 'none')",
            name="billing_state_enforcement_check",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)

    # Stripe customer/subscription identity
    stripe_customer_id = Column(Text, nullable=True)
    stripe_subscription_id = Column(Text, nullable=True)
    billing_email = Column(Text, nullable=True)

    # Subscription lifecycle
    subscription_status = Column(Text, nullable=False, default="pending")
    current_period_start = Column(TIMESTAMP(timezone=True), nullable=True)
    current_period_end = Column(TIMESTAMP(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)

    # Usage metering bookkeeping (microdollars)
    last_reported_total = Column(BigInteger, nullable=False, default=0)
    last_reported_period_start = Column(TIMESTAMP(timezone=True), nullable=True)

    # Payment lifecycle
    payment_failed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # User limit enforcement
    user_limit_enforcement = Column(Text, nullable=False, default="soft")

    # Optimistic locking aid (incremented on every update)
    version = Column(Integer, nullable=False, default=0)

    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"<BillingState(status={self.subscription_status!r}, "
            f"customer={self.stripe_customer_id!r}, version={self.version})>"
        )


class BillingStateAudit(Base):
    """Append-only field-change log for billing_state.

    One row per changed field per update. Callers pass a ``changed_by``
    string formatted as ``"webhook:event_type"`` or ``"scheduler:source_name"``
    and the Stripe event ID when available.
    """

    __tablename__ = "billing_state_audit"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    changed_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    changed_by = Column(Text, nullable=True)  # e.g. "webhook:subscription.updated"
    field_name = Column(Text, nullable=False)
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    stripe_event_id = Column(Text, nullable=True)

    def __repr__(self) -> str:  # noqa: D105
        return f"<BillingStateAudit(field={self.field_name!r}, " f"by={self.changed_by!r}, at={self.changed_at})>"
