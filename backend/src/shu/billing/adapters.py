"""Adapter implementations for billing protocols.

Provides:
- UsageProviderImpl: queries llm_usage table for billing
- Persistence callbacks for webhooks (update billing_state)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.models.llm_provider import LLMProvider, LLMUsage

if TYPE_CHECKING:
    from shu.billing.seat_service import SeatService

# =============================================================================
# UsageRecord / UsageSummary Implementations
# =============================================================================


@dataclass
class UsageRecordImpl:
    """Concrete implementation of UsageRecord."""

    timestamp: datetime
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    usage_type: str
    # Snapshot of model.model_name at insert time (SHU-727). Populated even
    # when model_id is NULL because the FK target was deleted, so downstream
    # displays can still show a human-readable name instead of "unknown".
    model_name: str | None = None


@dataclass
class ModelUsageImpl:
    """Concrete implementation of ModelUsage."""

    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    request_count: int
    # See UsageRecordImpl.model_name — same snapshot semantics.
    model_name: str | None = None


@dataclass
class UsageSummaryImpl:
    """Concrete implementation of UsageSummary."""

    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
    by_model: dict[str, ModelUsageImpl]


# =============================================================================
# UsageProvider Implementation
# =============================================================================


class UsageProviderImpl:
    """Queries llm_usage table for billing.

    For single-instance deployment, all usage in llm_usage belongs to
    the instance's billing customer.

    Usage:
        provider = UsageProviderImpl(db_session)
        summary = await provider.get_usage_summary(period_start, period_end)
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_usage_for_period(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> list[UsageRecordImpl]:
        """Get individual usage records for a billing period.

        Returns all llm_usage records in the date range.
        """
        result = await self._db.execute(
            select(LLMUsage)
            .join(LLMProvider, LLMUsage.provider_id == LLMProvider.id)
            .where(
                LLMUsage.created_at >= period_start,
                LLMUsage.created_at < period_end,
                LLMProvider.is_system_managed.is_(True),
            )
            .order_by(LLMUsage.created_at)
        )

        records = []
        for row in result.scalars():
            records.append(
                UsageRecordImpl(
                    timestamp=row.created_at,
                    model_id=row.model_id or "unknown",
                    model_name=row.model_name,
                    input_tokens=row.input_tokens or 0,
                    output_tokens=row.output_tokens or 0,
                    cost_usd=row.total_cost if row.total_cost is not None else Decimal("0"),
                    usage_type=row.request_type or "unknown",
                )
            )
        return records

    async def get_usage_summary(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> UsageSummaryImpl:
        """Get aggregated usage summary for a billing period.

        Returns totals and breakdown by model.
        """
        # Aggregate by model. Group on the snapshot model_name as well so rows
        # whose model_id FK was nulled out (provider/model deleted — SHU-727)
        # still surface a human-readable label instead of collapsing into a
        # single "unknown" bucket.
        result = await self._db.execute(
            select(
                LLMUsage.model_id,
                LLMUsage.model_name,
                func.sum(LLMUsage.input_tokens).label("input_tokens"),
                func.sum(LLMUsage.output_tokens).label("output_tokens"),
                func.sum(LLMUsage.total_cost).label("total_cost"),
                func.count(LLMUsage.id).label("request_count"),
            )
            .join(LLMProvider, LLMUsage.provider_id == LLMProvider.id)
            .where(
                LLMUsage.created_at >= period_start,
                LLMUsage.created_at < period_end,
                LLMProvider.is_system_managed.is_(True),
            )
            .group_by(LLMUsage.model_id, LLMUsage.model_name)
        )

        by_model: dict[str, ModelUsageImpl] = {}
        total_input = 0
        total_output = 0
        total_cost = Decimal("0")

        for row in result:
            model_id = row.model_id or "unknown"
            input_tokens = int(row.input_tokens or 0)
            output_tokens = int(row.output_tokens or 0)
            # Keep cost as Decimal — converting to float here loses precision
            # and accumulates rounding error across thousands of rows.
            cost = row.total_cost if row.total_cost is not None else Decimal("0")
            count = int(row.request_count or 0)

            # Key on (model_id, model_name) so two GROUP BY rows with the same
            # model_id but different snapshot model_name don't collide. This
            # matters when model_id is NULL (each deleted model keeps its own
            # bucket) AND for the theoretical case where llm_models.model_name
            # was renamed between two INSERTs against the same model_id.
            bucket_key = f"{model_id}:{row.model_name or 'unnamed'}"
            by_model[bucket_key] = ModelUsageImpl(
                model_id=model_id,
                model_name=row.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                request_count=count,
            )

            total_input += input_tokens
            total_output += output_tokens
            total_cost += cost

        return UsageSummaryImpl(
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=total_cost,
            by_model=by_model,
        )


# =============================================================================
# Persistence Callbacks for Webhooks
# =============================================================================


async def create_subscription_persistence_callback(
    db: AsyncSession,
):
    """Create callback for persisting subscription updates from Stripe webhooks.

    Stores subscription state in billing_state under a row-level lock.

    Usage in webhook handler:
        persist_fn = await create_subscription_persistence_callback(db)
        await persist_fn(update)
    """
    from shu.billing.schemas import SubscriptionUpdate
    from shu.billing.state_service import BillingStateService

    async def persist_subscription(
        update: SubscriptionUpdate,
        stripe_event_id: str | None = None,
    ) -> None:
        await BillingStateService.update(
            db,
            updates={
                "stripe_subscription_id": update.stripe_subscription_id,
                "stripe_customer_id": update.stripe_customer_id,
                "subscription_status": update.status,
                "current_period_start": update.current_period_start,
                "current_period_end": update.current_period_end,
                "cancel_at_period_end": update.cancel_at_period_end,
            },
            source="webhook:subscription_update",
            stripe_event_id=stripe_event_id,
        )

    return persist_subscription


async def create_payment_failed_callback(db: AsyncSession):
    """Create callback for persisting invoice.payment_failed events.

    Sets payment_failed_at to now so grace-period enforcement can compute
    whether the payment window has elapsed. The timestamp is only written
    when the field is currently NULL — Stripe emits multiple
    invoice.payment_failed events per delinquency cycle (dunning retries),
    and each retry must not reset the countdown start.
    """
    from datetime import UTC, datetime

    from shu.billing.state_service import BillingStateService

    async def on_payment_failed(
        stripe_customer_id: str,
        subscription_id: str,
        invoice_id: str,
        stripe_event_id: str | None = None,
    ) -> None:
        state = await BillingStateService.get(db)
        if state is None or state.payment_failed_at is not None:
            # Grace period already started; preserve the first failure timestamp.
            return
        await BillingStateService.update(
            db,
            updates={"payment_failed_at": datetime.now(UTC)},
            source="webhook:invoice.payment_failed",
            stripe_event_id=stripe_event_id,
        )

    return on_payment_failed


async def create_payment_recovered_callback(db: AsyncSession):
    """Create callback for persisting invoice.paid events.

    Clears payment_failed_at, confirming the customer's account is current.
    """
    from shu.billing.state_service import BillingStateService

    async def on_payment_recovered(
        stripe_customer_id: str,
        subscription_id: str,
        invoice_id: str,
        stripe_event_id: str | None = None,
    ) -> None:
        await BillingStateService.update(
            db,
            updates={"payment_failed_at": None},
            source="webhook:invoice.paid",
            stripe_event_id=stripe_event_id,
        )

    return on_payment_recovered


def create_cycle_rollover_callback(db: AsyncSession, seat_service: SeatService):
    """Create callback that invokes `SeatService.rollover` on cycle-rollover invoices.

    Only `billing_reason == "subscription_cycle"` triggers rollover — other
    reasons (create, update, manual) reuse the same invoice.paid event but
    must not touch seat state.
    """

    async def on_cycle_rollover(
        stripe_customer_id: str,
        subscription_id: str,
        invoice_id: str,
        stripe_event_id: str,
        billing_reason: str | None,
    ) -> None:
        if billing_reason != "subscription_cycle":
            return
        await seat_service.rollover(db, subscription_id, stripe_event_id)

    return on_cycle_rollover


async def get_billing_config(db: AsyncSession) -> dict:
    """Get current billing configuration from billing_state.

    Returns dict with keys matching the previous system_settings["billing"]
    schema so all callers continue to work without change. Datetime fields
    are serialised as ISO strings.
    """
    from shu.billing.state_service import BillingStateService

    state = await BillingStateService.get(db)
    if state is None:
        return {}
    return {
        "stripe_customer_id": state.stripe_customer_id,
        "stripe_subscription_id": state.stripe_subscription_id,
        "billing_email": state.billing_email,
        "subscription_status": state.subscription_status,
        "current_period_start": state.current_period_start.isoformat() if state.current_period_start else None,
        "current_period_end": state.current_period_end.isoformat() if state.current_period_end else None,
        "cancel_at_period_end": state.cancel_at_period_end,
        "last_reported_total": state.last_reported_total,
        "last_reported_period_start": state.last_reported_period_start.isoformat()
        if state.last_reported_period_start
        else None,
        "user_limit_enforcement": state.user_limit_enforcement,
    }


async def get_user_count(db: AsyncSession) -> int:
    """Get total user count (active + inactive) for limit enforcement."""
    from shu.auth.models import User

    result = await db.execute(select(func.count(User.id)))
    return result.scalar() or 0


async def get_active_user_count(db: AsyncSession) -> int:
    """Get active user count for Stripe billing quantity sync.

    Only active users are billable — pending registrations awaiting admin
    activation should not increase the subscription seat count.
    """
    from shu.auth.models import User

    result = await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))
    return result.scalar() or 0
