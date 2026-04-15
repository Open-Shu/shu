"""Billing service - main interface for billing operations.

This service coordinates between:
- StripeClient for Stripe API calls
- Webhook handlers for event processing
"""

from __future__ import annotations

import math
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.config import BillingSettings, get_billing_settings
from shu.billing.schemas import (
    PortalSessionResponse,
    SubscriptionUpdate,
    UsageMeterEvent,
)
from shu.billing.stripe_client import StripeClient, StripeClientError
from shu.billing.webhook_handlers import WebhookDispatcher
from shu.core.logging import get_logger

logger = get_logger(__name__)


PersistSubscriptionFn = Callable[..., Coroutine[Any, Any, None]]


class BillingService:
    """Main billing service interface.

    Provides high-level billing operations coordinating with Stripe.
    """

    def __init__(
        self,
        settings: BillingSettings | None = None,
        stripe_client: StripeClient | None = None,
    ) -> None:
        self._settings = settings or get_billing_settings()
        self._client = stripe_client or StripeClient(self._settings)
        self._dispatcher = WebhookDispatcher(self._client)

    @property
    def is_configured(self) -> bool:
        """Check if Stripe is properly configured."""
        return self._settings.is_configured

    # =========================================================================
    # Portal
    # =========================================================================

    async def create_portal_session(
        self,
        stripe_customer_id: str,
        return_url: str | None = None,
    ) -> PortalSessionResponse:
        """Create a Stripe Customer Portal session.

        Args:
            stripe_customer_id: The customer's Stripe ID
            return_url: Where to redirect after portal (optional)

        Returns:
            PortalSessionResponse with portal URL

        """
        url = return_url or f"{self._settings.app_base_url}/billing"
        return await self._client.create_portal_session(stripe_customer_id, url)

    async def sync_subscription_quantity(
        self,
        stripe_subscription_id: str,
        user_count: int,
        proration: Literal["create_prorations", "none", "always_invoice"] = "create_prorations",
    ) -> bool:
        """Sync the subscription quantity to match current user count.

        This should be called when users are added/removed.

        Args:
            stripe_subscription_id: The Stripe subscription ID
            user_count: Current user count
            proration: How to handle proration

        Returns:
            True if Stripe was updated, False if quantity already matched.

        Raises:
            StripeClientError: If the subscription is not found in Stripe,
                no item matches the configured seat price, or a Stripe API
                call fails. Callers must not persist local quantity on raise.

        """
        try:
            subscription = await self._client.get_subscription(stripe_subscription_id)
            if not subscription:
                raise StripeClientError(f"Subscription {stripe_subscription_id!r} not found in Stripe")

            # Subscriptions may carry multiple items (e.g., licensed seat + metered
            # cost). Match the seat item by price ID instead of assuming index 0.
            items_data = subscription.get("items", {}).get("data", [])
            seat_item = self._find_seat_item(items_data)
            if seat_item is None:
                raise StripeClientError(
                    "Subscription has no item matching the configured seat price",
                    # Not a Stripe API error — no underlying stripe_error to attach
                )

            current_quantity = seat_item.get("quantity", 0)

            if current_quantity == user_count:
                logger.debug(
                    "Subscription quantity already matches",
                    extra={"subscription_id": stripe_subscription_id, "quantity": user_count},
                )
                return False

            await self._client.update_subscription_quantity(
                stripe_subscription_id,
                user_count,
                proration,
            )

            logger.info(
                "Synced subscription quantity",
                extra={
                    "subscription_id": stripe_subscription_id,
                    "old_quantity": current_quantity,
                    "new_quantity": user_count,
                },
            )
            return True

        except StripeClientError as e:
            logger.error(
                "Failed to sync subscription quantity",
                extra={"subscription_id": stripe_subscription_id, "error": str(e)},
            )
            raise

    # =========================================================================
    # Usage Reporting
    # =========================================================================

    async def report_usage_to_stripe(
        self,
        stripe_customer_id: str,
        delta_cost_microdollars: int,
        period_start: datetime,
        period_end: datetime,
        cumulative_total_microdollars: int,
    ) -> bool:
        """Report a cost delta to Stripe Meters API for billing.

        Value is cost in microdollars (1 microdollar = $0.000001). Stripe
        Meters aggregate with SUM, so callers MUST send deltas — the
        compare-and-correct reconciliation in report_and_reconcile_usage
        handles this.

        Idempotency: the event identifier is derived from
        (customer, period_start, cumulative_total). On retry of the same
        cumulative position, Stripe dedupes via the identifier. New usage
        produces a new identifier and is counted.

        Args:
            stripe_customer_id: The Stripe customer ID
            delta_cost_microdollars: Cost delta in microdollars (positive integer)
            period_start: Start of the reporting window
            period_end: End of the reporting window
            cumulative_total_microdollars: Cumulative period total after this delta;
                used to construct the deterministic idempotency identifier.

        Returns:
            True if usage was reported successfully

        """
        if delta_cost_microdollars <= 0:
            logger.debug("No usage cost to report")
            return True

        # Deterministic identifier — same cumulative position → same identifier
        # → Stripe dedupes. Different cumulative position → different identifier
        # → counted as new usage.
        identifier = (
            f"shu-usage-{stripe_customer_id}-" f"{int(period_start.timestamp())}-{cumulative_total_microdollars}"
        )

        event = UsageMeterEvent(
            event_name=self._settings.meter_event_name,
            stripe_customer_id=stripe_customer_id,
            timestamp=int(period_end.timestamp()),
            value=delta_cost_microdollars,
            identifier=identifier,
            payload={
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
        )

        result = await self._client.report_usage(event)
        return result is not None

    async def report_and_reconcile_usage(
        self,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Compare-and-correct usage reporting to Stripe Meters.

        Queries both our llm_usage total and Stripe's meter summary for the
        current billing period, then sends only the gap. Self-correcting:
        any missed events from prior runs are caught automatically.

        Handles:
        - Stripe async processing lag (uses last_reported_total as floor)
        - Period rollover (catchup for old period before switching)
        - Crash recovery (Stripe summary eventually reflects sent events)

        Args:
            db: Database session for llm_usage queries and system_settings

        Returns:
            Status dict with keys: action, delta, our_total, stripe_total

        """
        from shu.billing.adapters import UsageProviderImpl, get_billing_config

        billing_config = await get_billing_config(db)
        customer_id = billing_config.get("stripe_customer_id")
        if not customer_id:
            return {"action": "skipped", "reason": "no_customer"}

        if not self._settings.meter_id_cost:
            return {"action": "skipped", "reason": "no_meter"}

        period_start_str = billing_config.get("current_period_start")

        if not period_start_str:
            return {"action": "skipped", "reason": "no_period"}
        period_start = datetime.fromisoformat(period_start_str)

        last_reported_total = billing_config.get("last_reported_total", 0)
        last_reported_period = billing_config.get("last_reported_period_start")

        # Period rollover: catchup old period, then reset
        if last_reported_period and last_reported_period != period_start_str:
            old_start = datetime.fromisoformat(last_reported_period)
            # Find old period end — use current period start as the boundary
            old_end = period_start
            catchup_ok = await self._catchup_old_period(db, customer_id, old_start, old_end, last_reported_total)
            if not catchup_ok:
                # Don't proceed to new-period reporting; that would overwrite
                # the old-period marker and drop the gap.
                return {
                    "action": "catchup_failed",
                    "old_period_start": last_reported_period,
                }
            last_reported_total = 0

        # Query our cumulative total for current period
        now = datetime.now(UTC)
        usage_provider = UsageProviderImpl(db)
        summary = await usage_provider.get_usage_summary(period_start, now)
        our_total = math.ceil(summary.total_cost_usd * 1_000_000)  # microdollars

        # Query Stripe's view (also in microdollars)
        stripe_total = await self._client.get_meter_event_summary(
            customer_id,
            start_time=int(period_start.timestamp()),
            end_time=int(now.timestamp()),
        )

        # Determine delta with async-lag protection.
        # If Stripe's total >= our last report, Stripe caught up — use Stripe's actual state.
        # Otherwise Stripe is still processing — use our bookkeeping to avoid double-counting.
        baseline = stripe_total if stripe_total >= last_reported_total else last_reported_total
        delta = our_total - baseline

        if delta <= 0:
            return {
                "action": "no_delta",
                "our_total": our_total,
                "stripe_total": stripe_total,
                "last_reported_total": last_reported_total,
            }

        # Report the delta. Pass our_total so the idempotency identifier
        # encodes the post-delta cumulative position.
        reported = await self.report_usage_to_stripe(
            stripe_customer_id=customer_id,
            delta_cost_microdollars=delta,
            period_start=period_start,
            period_end=now,
            cumulative_total_microdollars=our_total,
        )

        if reported:
            from shu.billing.state_service import BillingStateService

            await BillingStateService.update(
                db,
                updates={
                    "last_reported_total": our_total,
                    "last_reported_period_start": period_start,
                },
                source="scheduler:usage_reporting",
            )

            logger.info(
                "Usage reported to Stripe",
                extra={
                    "delta": delta,
                    "our_total": our_total,
                    "stripe_total": stripe_total,
                    "period_start": period_start.isoformat(),
                },
            )

        return {
            "action": "reported" if reported else "report_failed",
            "delta": delta,
            "our_total": our_total,
            "stripe_total": stripe_total,
        }

    async def _catchup_old_period(
        self,
        db: AsyncSession,
        customer_id: str,
        old_start: datetime,
        old_end: datetime,
        last_reported_total: int,
    ) -> bool:
        """Send any remaining usage for a completed billing period.

        Returns:
            True if catchup succeeded (or no gap existed); caller may proceed
            to new-period reporting. False if the Stripe report failed; caller
            must short-circuit so the next run retries the old-period catchup.

        """
        from shu.billing.adapters import UsageProviderImpl

        usage_provider = UsageProviderImpl(db)
        summary = await usage_provider.get_usage_summary(old_start, old_end)
        old_total = math.ceil(summary.total_cost_usd * 1_000_000)  # microdollars

        old_stripe_total = await self._client.get_meter_event_summary(
            customer_id,
            start_time=int(old_start.timestamp()),
            end_time=int(old_end.timestamp()),
        )

        delta = old_total - max(old_stripe_total, last_reported_total)
        if delta > 0:
            reported = await self.report_usage_to_stripe(
                stripe_customer_id=customer_id,
                delta_cost_microdollars=delta,
                period_start=old_start,
                period_end=old_end,
                cumulative_total_microdollars=old_total,
            )
            if not reported:
                # Leave last_reported_period_start intact so the next run
                # retries the old-period catchup instead of dropping the gap.
                logger.warning(
                    "Old period catchup report failed; will retry next run",
                    extra={"delta": delta, "old_period_start": old_start.isoformat()},
                )
                return False
            logger.info(
                "Old period catchup reported",
                extra={"delta": delta, "old_period_start": old_start.isoformat()},
            )

        # Reset for new period (only reached when catchup succeeded or no delta was needed)
        from shu.billing.state_service import BillingStateService

        await BillingStateService.update(
            db,
            updates={
                "last_reported_total": 0,
                "last_reported_period_start": None,
            },
            source="scheduler:usage_reporting_period_reset",
        )
        return True

    # =========================================================================
    # Webhooks
    # =========================================================================

    async def handle_webhook(
        self,
        payload: bytes,
        signature: str,
        persist_subscription: PersistSubscriptionFn | None = None,
        on_payment_failed: Any | None = None,
        on_payment_recovered: Any | None = None,
        expected_customer_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Process a Stripe webhook event.

        This verifies the webhook signature, dispatches to handlers, and
        uses the provided callbacks to persist state changes.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header
            persist_subscription: Callback to save subscription changes
            on_payment_failed: Callback to set payment_failed_at on invoice failure
            on_payment_recovered: Callback to clear payment_failed_at on invoice paid
            expected_customer_id: Customer ID from billing_state. When set,
                events for other customers are silently dropped (multi-instance
                safety: all instances on a shared Stripe account receive all
                events). When None the instance is misconfigured — all events
                are dropped with a warning.

        Returns:
            Tuple of (handled: bool, event_type: str, event_id: str | None)

        """
        try:
            event = self._client.construct_webhook_event(payload, signature)
        except StripeClientError as e:
            logger.warning("Webhook verification failed", extra={"error": str(e)})
            raise

        logger.info(
            "Received webhook",
            extra={"event_type": event.type, "event_id": event.id},
        )

        # Guard: SHU_STRIPE_CUSTOMER_ID must be configured before webhooks
        # can be processed. Without it this instance has no tenant identity.
        if expected_customer_id is None:
            logger.warning(
                "Ignoring webhook — SHU_STRIPE_CUSTOMER_ID not configured",
                extra={"event_type": event.type, "event_id": event.id},
            )
            return False, event.type, event.id

        # Multi-instance safety: all instances on a shared Stripe account
        # receive all events. Only process events for our own customer.
        event_customer = self._extract_customer_id(event)
        if event_customer and event_customer != expected_customer_id:
            logger.info(
                "Ignoring webhook for different customer",
                extra={
                    "event_type": event.type,
                    "event_customer": event_customer,
                    "expected_customer": expected_customer_id,
                },
            )
            return False, event.type, event.id

        callbacks: dict[str, Any] = {}

        if persist_subscription:

            async def on_subscription_update(update: SubscriptionUpdate) -> None:
                await persist_subscription(update, stripe_event_id=event.id)

            callbacks["on_subscription_update"] = on_subscription_update

        if on_payment_failed:
            callbacks["on_payment_failed"] = on_payment_failed

        if on_payment_recovered:
            callbacks["on_payment_recovered"] = on_payment_recovered

        handled = await self._dispatcher.dispatch(event, **callbacks)

        return handled, event.type, event.id

    def _find_seat_item(self, items_data: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find the subscription item that matches the configured seat price.

        Subscriptions may carry multiple items (e.g., licensed seat price plus
        a metered cost price). Quantity sync only applies to the seat item.
        """
        seat_price_id = self._settings.price_id_monthly
        if not seat_price_id:
            return items_data[0] if items_data else None
        for item in items_data:
            price = item.get("price")
            price_id = price.get("id") if isinstance(price, dict) else None
            if price_id == seat_price_id:
                return item
        return None

    @staticmethod
    def _extract_customer_id(event: Any) -> str | None:
        """Extract the Stripe customer ID from a webhook event's data.object."""
        data_obj = getattr(event, "data", None)
        if data_obj is None:
            return None
        obj = getattr(data_obj, "object", None) or {}
        # Most event types put customer directly on data.object
        customer = obj.get("customer") if isinstance(obj, dict) else getattr(obj, "customer", None)
        if customer:
            return str(customer)
        # customer.* events: the object IS the customer
        obj_type = obj.get("object") if isinstance(obj, dict) else getattr(obj, "object", None)
        if obj_type == "customer":
            cid = obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", None)
            return str(cid) if cid else None
        return None

    @property
    def supported_webhook_events(self) -> list[str]:
        """List of webhook event types this service handles."""
        return self._dispatcher.supported_events
