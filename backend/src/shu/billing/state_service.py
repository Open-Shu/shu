"""Billing state service — single entry point for all billing_state mutations.

All code that needs to read or write billing state must go through this
service. Direct ORM writes on BillingState bypass the row-level lock and
the audit trail, both of which this service enforces.

Locking strategy
----------------
``update()`` uses ``SELECT ... FOR UPDATE`` so that concurrent webhook
handlers serialise writes at the database level. Two handlers arriving
within milliseconds of each other will each acquire the lock in turn —
neither will lose its update to the other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger
from shu.models.billing_state import BillingState, BillingStateAudit

logger = get_logger(__name__)


def _to_json(value: Any) -> Any:
    """Convert a Python value to a JSON-serialisable form for the audit log."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class BillingStateService:
    """Read and mutate the billing_state singleton with locking and auditing.

    All methods are static — there's no per-instance state; the database
    session is passed explicitly so callers can share their existing session.
    """

    @staticmethod
    async def get(db: AsyncSession) -> BillingState | None:
        """Return the singleton billing state row, or None if not yet created."""
        result = await db.execute(
            select(BillingState).where(BillingState.id == 1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def ensure_singleton(db: AsyncSession) -> BillingState:
        """Create the singleton row if it doesn't exist, then return it.

        Safe to call on every startup, including concurrent multi-worker
        deployments. If two processes race to INSERT the same row, the loser
        catches the IntegrityError via a SAVEPOINT, rolls back just that
        nested write, and fetches the row the winner already inserted.
        The outer session transaction is never aborted.
        """
        state = await BillingStateService.get(db)
        if state is None:
            try:
                async with db.begin_nested():
                    state = BillingState(id=1)
                    db.add(state)
                    await db.flush()
                logger.info("billing_state singleton created")
            except IntegrityError:
                # Another worker won the race — fetch the row it inserted.
                state = await BillingStateService.get(db)
        return state

    @staticmethod
    async def update(
        db: AsyncSession,
        updates: dict[str, Any],
        source: str,
        stripe_event_id: str | None = None,
    ) -> BillingState:
        """Apply ``updates`` to the singleton row under a row-level lock.

        Acquires ``SELECT ... FOR UPDATE`` so concurrent callers serialise.
        Writes one ``BillingStateAudit`` row per changed field.

        Args:
            db: Async session. MUST be in an active transaction (the lock
                is held until the transaction commits or rolls back).
            updates: Dict of column-name → new-value pairs to apply.
            source: Human-readable source label, e.g.
                ``"webhook:customer.subscription.updated"`` or
                ``"scheduler:usage_reporting"``.
            stripe_event_id: Stripe event ID if triggered by a webhook.

        Returns:
            The updated ``BillingState`` instance.

        Raises:
            RuntimeError: If the singleton row doesn't exist (startup sequencing
                error — ensure_singleton() must be called before any updates).

        """
        result = await db.execute(
            select(BillingState).where(BillingState.id == 1).with_for_update()
        )
        state = result.scalar_one_or_none()
        if state is None:
            raise RuntimeError(
                "billing_state singleton row missing — "
                "call ensure_singleton() at startup"
            )

        now = datetime.now(UTC)

        for field, new_v in updates.items():
            if not hasattr(state, field):
                logger.warning(
                    "Unknown billing_state field in update",
                    extra={"field": field, "source": source},
                )
                continue
            old_v = getattr(state, field)
            if old_v != new_v:
                db.add(
                    BillingStateAudit(
                        changed_by=source,
                        field_name=field,
                        old_value=_to_json(old_v),
                        new_value=_to_json(new_v),
                        stripe_event_id=stripe_event_id,
                        changed_at=now,
                    )
                )
            setattr(state, field, new_v)

        state.version += 1
        state.updated_at = now
        await db.commit()
        return state
