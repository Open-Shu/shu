"""Billing service - main interface for billing operations.

This service coordinates between:
- StripeClient for Stripe API calls
- Webhook handlers for event processing

For single-instance deployment, billing config is stored in system_settings.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.config import BillingSettings, get_billing_settings
from shu.billing.schemas import (
    CheckoutSessionCreate,
    CheckoutSessionResponse,
    PortalSessionResponse,
    StripeCustomerData,
    SubscriptionUpdate,
    UsageMeterEvent,
)
from shu.billing.stripe_client import StripeClient, StripeClientError
from shu.billing.webhook_handlers import WebhookDispatcher
from shu.core.logging import get_logger

logger = get_logger(__name__)


# Type aliases for persistence callbacks
PersistSubscriptionFn = Callable[[SubscriptionUpdate], Coroutine[Any, Any, None]]
PersistCustomerLinkFn = Callable[[str, str, str | None], Coroutine[Any, Any, bool]]
# (stripe_customer_id, email, subscription_id) -> success


class BillingService:
    """Main billing service interface.

    Provides high-level billing operations coordinating with Stripe.

    Example usage:
        settings = get_billing_settings()
        service = BillingService(settings)

        session = await service.create_checkout_session(
            request=CheckoutSessionCreate(quantity=5),
            customer_email="user@example.com",
        )
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
    # Checkout & Subscriptions
    # =========================================================================

    async def create_checkout_session(
        self,
        request: CheckoutSessionCreate,
        customer_email: str | None = None,
        stripe_customer_id: str | None = None,
    ) -> CheckoutSessionResponse:
        """Create a Stripe Checkout session for new subscription.

        Args:
            request: Checkout request with quantity and optional metadata
            customer_email: Email to pre-fill in Checkout
            stripe_customer_id: Existing Stripe customer ID (if known)

        Returns:
            CheckoutSessionResponse with redirect URL

        """
        success_url = request.success_url or f"{self._settings.app_base_url}/billing/success"
        cancel_url = request.cancel_url or f"{self._settings.app_base_url}/billing/cancel"

        metadata = dict(request.metadata or {})

        return self._client.create_checkout_session(
            customer_id=stripe_customer_id,
            customer_email=customer_email or request.customer_email,
            quantity=request.quantity,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
        )

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
        return self._client.create_portal_session(stripe_customer_id, url)

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
            True if quantity was updated, False if no update needed

        """
        try:
            subscription = self._client.get_subscription(stripe_subscription_id)
            if not subscription:
                return False

            # Subscriptions may carry multiple items (e.g., licensed seat + metered
            # cost). Match the seat item by price ID instead of assuming index 0.
            items_data = subscription.get("items", {}).get("data", [])
            seat_item = self._find_seat_item(items_data)
            if seat_item is None:
                logger.error(
                    "Subscription has no item matching the configured seat price",
                    extra={
                        "subscription_id": stripe_subscription_id,
                        "configured_price": self._settings.price_id_monthly,
                    },
                )
                return False

            current_quantity = seat_item.get("quantity", 0)

            if current_quantity == user_count:
                logger.debug(
                    "Subscription quantity already matches",
                    extra={"subscription_id": stripe_subscription_id, "quantity": user_count},
                )
                return False

            self._client.update_subscription_quantity(
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
    # Customer Management
    # =========================================================================

    async def create_stripe_customer(
        self,
        email: str,
        name: str,
    ) -> str:
        """Create a new Stripe customer.

        Args:
            email: Customer email
            name: Customer/company name

        Returns:
            Stripe customer ID

        """
        stripe_customer = self._client.create_customer(
            StripeCustomerData(
                email=email,
                name=name,
                metadata={},
            )
        )
        return stripe_customer.id

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
            f"shu-usage-{stripe_customer_id}-"
            f"{int(period_start.timestamp())}-{cumulative_total_microdollars}"
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

        result = self._client.report_usage(event)
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
        from shu.billing.adapters import BILLING_SETTINGS_KEY, UsageProviderImpl, get_billing_config
        from shu.services.system_settings_service import SystemSettingsService

        billing_config = await get_billing_config(db)
        customer_id = billing_config.get("stripe_customer_id")
        if not customer_id:
            return {"action": "skipped", "reason": "no_customer"}

        if not self._settings.meter_id_cost:
            return {"action": "skipped", "reason": "no_meter"}

        period_start_str = billing_config.get("current_period_start")

        if period_start_str:
            period_start = datetime.fromisoformat(period_start_str)
        else:
            # Fall back to start of current month
            now = datetime.now(UTC)
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        settings_service = SystemSettingsService(db)
        last_reported_total = billing_config.get("last_reported_total", 0)
        last_reported_period = billing_config.get("last_reported_period_start")

        # Period rollover: catchup old period, then reset
        if last_reported_period and last_reported_period != period_start_str:
            old_start = datetime.fromisoformat(last_reported_period)
            # Find old period end — use current period start as the boundary
            old_end = period_start
            catchup_ok = await self._catchup_old_period(
                db, customer_id, old_start, old_end, last_reported_total, billing_config, settings_service
            )
            if not catchup_ok:
                # Don't proceed to new-period reporting; that would overwrite
                # the old-period marker in system_settings and drop the gap.
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
        stripe_total = self._client.get_meter_event_summary(
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
            billing_config["last_reported_total"] = our_total
            billing_config["last_reported_period_start"] = period_start.isoformat()
            await settings_service.upsert(BILLING_SETTINGS_KEY, billing_config)

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
        billing_config: dict,
        settings_service: Any,
    ) -> bool:
        """Send any remaining usage for a completed billing period.

        Returns:
            True if catchup succeeded (or no gap existed); caller may proceed
            to new-period reporting. False if the Stripe report failed; caller
            must short-circuit so the next run retries the old-period catchup.

        """
        from shu.billing.adapters import BILLING_SETTINGS_KEY, UsageProviderImpl

        usage_provider = UsageProviderImpl(db)
        summary = await usage_provider.get_usage_summary(old_start, old_end)
        old_total = math.ceil(summary.total_cost_usd * 1_000_000)  # microdollars

        old_stripe_total = self._client.get_meter_event_summary(
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
        billing_config["last_reported_total"] = 0
        billing_config["last_reported_period_start"] = None
        await settings_service.upsert(BILLING_SETTINGS_KEY, billing_config)
        return True

    # =========================================================================
    # Webhooks
    # =========================================================================

    async def handle_webhook(
        self,
        payload: bytes,
        signature: str,
        persist_subscription: PersistSubscriptionFn | None = None,
        persist_customer_link: PersistCustomerLinkFn | None = None,
        expected_customer_id: str | None = None,
        pending_checkout_session_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Process a Stripe webhook event.

        This verifies the webhook signature, dispatches to handlers, and
        uses the provided callbacks to persist changes.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header
            persist_subscription: Callback to save subscription changes
            persist_customer_link: Callback to link Stripe customer
            expected_customer_id: If set, reject events for other customers.
                When multiple Shu instances share a Stripe account, each
                endpoint receives ALL events. This filter ensures an instance
                only processes events for its own customer after the initial
                link has been established.
            pending_checkout_session_id: If set, only accept
                checkout.session.completed events for this session ID. This
                prevents a fresh instance (no expected_customer_id yet) from
                binding itself to another tenant's customer.

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

        # Instance-binding safety for fresh (unlinked) instances.
        # Before a customer is linked (expected_customer_id is None), any
        # customer.created or checkout.session.completed on the shared Stripe
        # account could bind this instance to the wrong tenant. Only accept
        # checkout.session.completed matching our pending session claim; drop
        # customer.created and rely on the verified checkout event to link.
        if expected_customer_id is None:
            if event.type == "checkout.session.completed":
                data_obj = getattr(event.data, "object", None) or {}
                session_id = data_obj.get("id") if isinstance(data_obj, dict) else getattr(data_obj, "id", None)
                if not pending_checkout_session_id or session_id != pending_checkout_session_id:
                    logger.info(
                        "Ignoring checkout.session.completed — no matching pending session",
                        extra={
                            "event_session_id": session_id,
                            "pending_session_id": pending_checkout_session_id,
                        },
                    )
                    return False, event.type, event.id
            elif event.type == "customer.created":
                # customer.created has no session context; unsafe to act on
                # before we've verified a checkout session. The matching
                # checkout.session.completed is the authoritative linker.
                logger.info(
                    "Ignoring customer.created — instance not yet linked",
                    extra={"event_id": event.id},
                )
                return False, event.type, event.id

        # Scope check: reject events for other customers (multi-instance safety).
        # Applies once a customer is linked — then we only process events
        # matching that customer.
        if expected_customer_id:
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

        # Build callback wrappers
        callbacks: dict[str, Any] = {}

        if persist_subscription:
            async def on_subscription_update(update: SubscriptionUpdate) -> None:
                await persist_subscription(update)

            callbacks["on_subscription_update"] = on_subscription_update

        if persist_customer_link:
            async def on_checkout_completed(data: dict[str, Any]) -> None:
                customer_id = data.get("customer_id")
                email = data.get("customer_email")
                subscription_id = data.get("subscription_id")
                if customer_id and email:
                    await persist_customer_link(customer_id, email, subscription_id)

            callbacks["on_checkout_completed"] = on_checkout_completed

            async def on_customer_created(customer_id: str, email: str) -> None:
                await persist_customer_link(customer_id, email, None)

            callbacks["on_customer_created"] = on_customer_created

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
