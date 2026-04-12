"""Adapter implementations for billing protocols.

Provides:
- UsageProviderImpl: queries llm_usage table for billing
- Persistence callbacks for webhooks (update system_settings)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.protocols import UsageRecord, UsageSummary
from shu.models.llm_provider import LLMUsage

# =============================================================================
# Billing Settings Key
# =============================================================================

BILLING_SETTINGS_KEY = "billing"


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


@dataclass
class ModelUsageImpl:
    """Concrete implementation of ModelUsage."""

    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    request_count: int


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
    ) -> list[UsageRecord]:
        """Get individual usage records for a billing period.

        Returns all llm_usage records in the date range.
        """
        result = await self._db.execute(
            select(LLMUsage)
            .where(
                LLMUsage.created_at >= period_start,
                LLMUsage.created_at < period_end,
            )
            .order_by(LLMUsage.created_at)
        )

        records = []
        for row in result.scalars():
            records.append(
                UsageRecordImpl(
                    timestamp=row.created_at,
                    model_id=row.model_id or "unknown",
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
    ) -> UsageSummary:
        """Get aggregated usage summary for a billing period.

        Returns totals and breakdown by model.
        """
        # Aggregate by model
        result = await self._db.execute(
            select(
                LLMUsage.model_id,
                func.sum(LLMUsage.input_tokens).label("input_tokens"),
                func.sum(LLMUsage.output_tokens).label("output_tokens"),
                func.sum(LLMUsage.total_cost).label("total_cost"),
                func.count(LLMUsage.id).label("request_count"),
            )
            .where(
                LLMUsage.created_at >= period_start,
                LLMUsage.created_at < period_end,
            )
            .group_by(LLMUsage.model_id)
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

            by_model[model_id] = ModelUsageImpl(
                model_id=model_id,
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

    Stores subscription state in system_settings under the 'billing' key.

    Usage in webhook handler:
        persist_fn = await create_subscription_persistence_callback(db)
        await persist_fn(update)
    """
    from shu.billing.schemas import SubscriptionUpdate
    from shu.services.system_settings_service import SystemSettingsService

    settings_service = SystemSettingsService(db)

    async def persist_subscription(update: SubscriptionUpdate) -> None:
        current = await settings_service.get_value(BILLING_SETTINGS_KEY, {}) or {}
        current.update({
            "stripe_subscription_id": update.stripe_subscription_id,
            "stripe_customer_id": update.stripe_customer_id,
            "subscription_status": update.status,
            "current_period_start": update.current_period_start.isoformat() if update.current_period_start else None,
            "current_period_end": update.current_period_end.isoformat() if update.current_period_end else None,
            "quantity": update.quantity,
            "cancel_at_period_end": update.cancel_at_period_end,
        })
        await settings_service.upsert(BILLING_SETTINGS_KEY, current)

    return persist_subscription


async def create_customer_link_callback(
    db: AsyncSession,
):
    """Create callback for linking Stripe customers from checkout completion.

    Stores the Stripe customer ID in system_settings.
    """
    from shu.services.system_settings_service import SystemSettingsService

    settings_service = SystemSettingsService(db)

    async def persist_customer_link(
        stripe_customer_id: str,
        email: str,
        subscription_id: str | None,
    ) -> bool:
        current = await settings_service.get_value(BILLING_SETTINGS_KEY, {}) or {}
        current.update({
            "stripe_customer_id": stripe_customer_id,
            "billing_email": email,
        })
        if subscription_id:
            current["stripe_subscription_id"] = subscription_id
        # Clear the pending checkout claim now that the link is established.
        current.pop("pending_checkout_session_id", None)
        await settings_service.upsert(BILLING_SETTINGS_KEY, current)
        return True

    return persist_customer_link


async def get_billing_config(db: AsyncSession) -> dict:
    """Get current billing configuration from system_settings.

    Returns dict with keys:
        - stripe_customer_id
        - stripe_subscription_id
        - subscription_status
        - current_period_start
        - current_period_end
        - user_limit
    """
    from shu.services.system_settings_service import SystemSettingsService

    settings_service = SystemSettingsService(db)
    return await settings_service.get_value(BILLING_SETTINGS_KEY, {}) or {}


async def get_user_count(db: AsyncSession) -> int:
    """Get current user count for billing purposes."""
    from shu.auth.models import User

    result = await db.execute(select(func.count(User.id)))
    return result.scalar() or 0
