"""Adapter implementations for billing protocols.

Provides:
- UsageProviderImpl: queries llm_usage table for billing
- create_cycle_rollover_callback: invokes SeatService.rollover on
  cycle-rollover invoice.paid webhooks (subscription / payment status
  persistence lifted to CP in SHU-774).
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


@dataclass
class DailyModelUsageImpl:
    """One (UTC day, model) bucket for the My Usage time-series (SHU-844)."""

    day: datetime
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    request_count: int
    # See UsageRecordImpl.model_name — same snapshot semantics.
    model_name: str | None = None


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

    @staticmethod
    def _billable_period_conditions(
        period_start: datetime,
        period_end: datetime,
        user_id: str | None,
    ) -> list:
        """WHERE clauses shared by the period aggregations.

        Filters to billable (Shu-managed) providers within the half-open
        ``[start, end)`` window. When ``user_id`` is given, scopes to that one
        user — the per-user "My Usage" path (SHU-844). Tenant isolation is
        enforced by RLS regardless of this filter.
        """
        conditions = [
            LLMUsage.created_at >= period_start,
            LLMUsage.created_at < period_end,
            LLMProvider.is_system_managed.is_(True),
        ]
        if user_id is not None:
            conditions.append(LLMUsage.user_id == user_id)
        return conditions

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
        *,
        user_id: str | None = None,
    ) -> UsageSummaryImpl:
        """Get aggregated usage summary for a billing period.

        Returns totals and breakdown by model. When ``user_id`` is provided the
        summary is scoped to that single user (the per-user "My Usage" view,
        SHU-844); otherwise it is the whole tenant (the admin dashboard).
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
            .where(*self._billable_period_conditions(period_start, period_end, user_id))
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

    async def get_daily_usage_for_user(
        self,
        user_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> list[DailyModelUsageImpl]:
        """Per-(day, model) billable usage for one user over the period.

        Powers the My Usage time-series chart (SHU-844). Days are bucketed with
        ``date_trunc('day', created_at)``; ``created_at`` is stored UTC-naive,
        so buckets are UTC calendar days — the frontend labels them as such.
        Returns the raw per-(day, model) rows; the frontend pivots them into
        per-model chart series.
        """
        day = func.date_trunc("day", LLMUsage.created_at).label("day")
        result = await self._db.execute(
            select(
                day,
                LLMUsage.model_id,
                LLMUsage.model_name,
                func.sum(LLMUsage.input_tokens).label("input_tokens"),
                func.sum(LLMUsage.output_tokens).label("output_tokens"),
                func.sum(LLMUsage.total_cost).label("total_cost"),
                func.count(LLMUsage.id).label("request_count"),
            )
            .join(LLMProvider, LLMUsage.provider_id == LLMProvider.id)
            .where(*self._billable_period_conditions(period_start, period_end, user_id))
            .group_by(day, LLMUsage.model_id, LLMUsage.model_name)
            .order_by(day)
        )

        return [
            DailyModelUsageImpl(
                day=row.day,
                model_id=row.model_id or "unknown",
                model_name=row.model_name,
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                # Decimal preserved to the API boundary, same as get_usage_summary.
                cost_usd=row.total_cost if row.total_cost is not None else Decimal("0"),
                request_count=int(row.request_count or 0),
            )
            for row in result
        ]


# =============================================================================
# Persistence Callbacks for Webhooks
# =============================================================================


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

    Returns only the columns that are still actively written to the local
    row. SHU-774 lifted subscription/payment-status persistence to CP, so
    `subscription_status`, `current_period_*`, `cancel_at_period_end`, and
    `payment_failed_at` are no longer included — readers must source those
    from `get_current_billing_state()` (the CP cache) instead.
    """
    from shu.billing.state_service import BillingStateService

    state = await BillingStateService.get(db)
    if state is None:
        return {}
    return {
        "stripe_customer_id": state.stripe_customer_id,
        "stripe_subscription_id": state.stripe_subscription_id,
        "billing_email": state.billing_email,
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
