"""Stripe SDK wrapper.

Encapsulates all direct Stripe API interactions. The rest of the billing
module uses this client rather than importing stripe directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import stripe
from stripe import Customer, Subscription

from shu.billing.config import BillingSettings, get_billing_settings
from shu.billing.schemas import (
    CheckoutSessionResponse,
    PortalSessionResponse,
    StripeCustomerData,
    SubscriptionUpdate,
    UsageMeterEvent,
)
from shu.core.logging import get_logger

logger = get_logger(__name__)


class StripeClientError(Exception):
    """Base exception for Stripe client errors."""

    def __init__(self, message: str, stripe_error: stripe.StripeError | None = None) -> None:
        super().__init__(message)
        self.stripe_error = stripe_error


class StripeConfigurationError(StripeClientError):
    """Raised when Stripe is not properly configured."""

    pass


class StripeClient:
    """Wrapper around the Stripe SDK.

    All Stripe API calls go through this client to:
    - Centralize configuration
    - Provide consistent error handling
    - Enable easier testing/mocking
    - Abstract SDK version changes
    """

    def __init__(self, settings: BillingSettings | None = None) -> None:
        self._settings = settings or get_billing_settings()
        self._validate_config()
        self._configure_stripe()

    def _validate_config(self) -> None:
        """Validate that required configuration is present."""
        if not self._settings.secret_key:
            raise StripeConfigurationError(
                "Stripe secret key not configured. Set SHU_STRIPE_SECRET_KEY."
            )

    def _configure_stripe(self) -> None:
        """Configure the Stripe SDK with our settings."""
        stripe.api_key = self._settings.secret_key
        # Set app info for Stripe Dashboard identification
        stripe.set_app_info(
            "Shu",
            version="1.0.0",
            url="https://github.com/Open-Shu/shu",
        )

    # =========================================================================
    # Customers
    # =========================================================================

    def create_customer(self, data: StripeCustomerData) -> Customer:
        """Create a new Stripe customer.

        Args:
            data: Customer data including email, name, metadata

        Returns:
            Created Stripe Customer object

        """
        try:
            customer = stripe.Customer.create(
                email=data.email,
                name=data.name,
                metadata=data.metadata,
            )
            logger.info(
                "Created Stripe customer",
                extra={"stripe_customer_id": customer.id, "email": data.email},
            )
            return customer
        except stripe.StripeError as e:
            logger.error(
                "Failed to create Stripe customer",
                extra={"email": data.email, "error": str(e)},
            )
            raise StripeClientError(f"Failed to create customer: {e}", e) from e

    def get_customer(self, customer_id: str) -> Customer | None:
        """Retrieve a Stripe customer by ID.

        Returns None if customer doesn't exist.
        """
        try:
            return stripe.Customer.retrieve(customer_id)
        except stripe.InvalidRequestError as e:
            if "No such customer" in str(e):
                return None
            raise StripeClientError(f"Failed to retrieve customer: {e}", e) from e
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to retrieve customer: {e}", e) from e

    def update_customer(self, customer_id: str, **kwargs: Any) -> Customer:
        """Update a Stripe customer."""
        try:
            return stripe.Customer.modify(customer_id, **kwargs)
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to update customer: {e}", e) from e

    # =========================================================================
    # Checkout Sessions
    # =========================================================================

    def create_checkout_session(
        self,
        customer_id: str | None,
        customer_email: str | None,
        quantity: int,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str] | None = None,
    ) -> CheckoutSessionResponse:
        """Create a Stripe Checkout session for subscription signup.

        Args:
            customer_id: Existing Stripe customer ID (if any)
            customer_email: Email to pre-fill (if no customer_id)
            quantity: Number of seats
            success_url: Redirect URL after successful payment
            cancel_url: Redirect URL if user cancels
            metadata: Additional metadata for the subscription

        Returns:
            CheckoutSessionResponse with session ID and URL

        """
        if not self._settings.price_id_monthly:
            raise StripeConfigurationError("Price ID not configured")

        try:
            params: dict[str, Any] = {
                "mode": "subscription",
                "line_items": [
                    {
                        "price": self._settings.price_id_monthly,
                        "quantity": quantity,
                    }
                ],
                "success_url": success_url,
                "cancel_url": cancel_url,
                "subscription_data": {
                    "metadata": metadata or {},
                },
            }

            # Link to existing customer or create new
            if customer_id:
                params["customer"] = customer_id
            elif customer_email:
                params["customer_email"] = customer_email

            session = stripe.checkout.Session.create(**params)

            logger.info(
                "Created checkout session",
                extra={
                    "session_id": session.id,
                    "customer_id": customer_id,
                    "quantity": quantity,
                },
            )

            return CheckoutSessionResponse(
                session_id=session.id,
                url=session.url or "",
            )

        except stripe.StripeError as e:
            logger.error("Failed to create checkout session", extra={"error": str(e)})
            raise StripeClientError(f"Failed to create checkout session: {e}", e) from e

    # =========================================================================
    # Customer Portal
    # =========================================================================

    def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> PortalSessionResponse:
        """Create a Stripe Customer Portal session.

        The portal allows customers to manage their subscription, payment
        methods, and view invoices.

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            PortalSessionResponse with portal URL

        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )

            logger.info(
                "Created portal session",
                extra={"customer_id": customer_id},
            )

            return PortalSessionResponse(url=session.url)

        except stripe.StripeError as e:
            logger.error(
                "Failed to create portal session",
                extra={"customer_id": customer_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to create portal session: {e}", e) from e

    # =========================================================================
    # Subscriptions
    # =========================================================================

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        """Retrieve a subscription by ID."""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except stripe.InvalidRequestError as e:
            if "No such subscription" in str(e):
                return None
            raise StripeClientError(f"Failed to retrieve subscription: {e}", e) from e
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to retrieve subscription: {e}", e) from e

    def update_subscription_quantity(
        self,
        subscription_id: str,
        quantity: int,
        proration_behavior: str = "create_prorations",
    ) -> Subscription:
        """Update the quantity (seats) on a subscription.

        Args:
            subscription_id: Stripe subscription ID
            quantity: New seat count
            proration_behavior: How to handle proration
                - 'create_prorations': Generate prorated line items (default)
                - 'none': Don't prorate
                - 'always_invoice': Immediately invoice for proration

        Returns:
            Updated Subscription object

        """
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)

            # Get the subscription item ID (assuming single line item)
            if not subscription.get("items", {}).get("data"):
                raise StripeClientError("Subscription has no items")

            item_id = subscription["items"]["data"][0]["id"]

            updated = stripe.Subscription.modify(
                subscription_id,
                items=[{"id": item_id, "quantity": quantity}],
                proration_behavior=proration_behavior,
            )

            logger.info(
                "Updated subscription quantity",
                extra={
                    "subscription_id": subscription_id,
                    "new_quantity": quantity,
                    "proration": proration_behavior,
                },
            )

            return updated

        except stripe.StripeError as e:
            logger.error(
                "Failed to update subscription quantity",
                extra={"subscription_id": subscription_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to update subscription: {e}", e) from e

    def cancel_subscription(
        self,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> Subscription:
        """Cancel a subscription.

        Args:
            subscription_id: Stripe subscription ID
            at_period_end: If True, cancel at end of current period (default).
                          If False, cancel immediately.

        """
        try:
            if at_period_end:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True,
                )
            else:
                subscription = stripe.Subscription.cancel(subscription_id)

            logger.info(
                "Canceled subscription",
                extra={
                    "subscription_id": subscription_id,
                    "at_period_end": at_period_end,
                },
            )

            return subscription

        except stripe.StripeError as e:
            logger.error(
                "Failed to cancel subscription",
                extra={"subscription_id": subscription_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to cancel subscription: {e}", e) from e

    # =========================================================================
    # Usage Metering (Stripe Billing Meters)
    # =========================================================================

    def report_usage(self, event: UsageMeterEvent) -> Any:
        """Report usage to Stripe Meters API.

        This is used for usage-based billing (token overage).

        Args:
            event: Usage event data including customer, timestamp, value

        Returns:
            Created MeterEvent or None if meters not configured

        """
        if not self._settings.meter_id_cost:
            logger.debug("Meter ID not configured, skipping usage report")
            return None

        try:
            meter_event = stripe.billing.MeterEvent.create(
                event_name=event.event_name,
                payload={
                    "stripe_customer_id": event.stripe_customer_id,
                    "value": str(event.value),
                    **event.payload,
                },
                timestamp=event.timestamp,
            )

            logger.debug(
                "Reported usage to Stripe",
                extra={
                    "customer_id": event.stripe_customer_id,
                    "value": event.value,
                    "meter_event_id": meter_event.identifier,
                },
            )

            return meter_event

        except stripe.StripeError as e:
            # Usage reporting failures should not break the app
            logger.error(
                "Failed to report usage to Stripe",
                extra={
                    "customer_id": event.stripe_customer_id,
                    "value": event.value,
                    "error": str(e),
                },
            )
            return None

    def get_meter_event_summary(
        self,
        customer_id: str,
        start_time: int,
        end_time: int,
    ) -> int:
        """Get aggregated meter event total for a customer in a time range.

        Queries Stripe's Meter Event Summaries API to find out how much
        usage Stripe has recorded. Used for compare-and-correct reconciliation.

        Args:
            customer_id: Stripe customer ID
            start_time: Unix timestamp for range start
            end_time: Unix timestamp for range end

        Returns:
            Aggregated cost total (in microdollars) from Stripe, or 0 if no data / meter not configured.

        """
        if not self._settings.meter_id_cost:
            return 0

        try:
            summaries = stripe.billing.Meter.list_event_summaries(
                self._settings.meter_id_cost,
                customer=customer_id,
                start_time=start_time,
                end_time=end_time,
            )

            total = 0
            for summary in summaries.auto_paging_iter():
                total += int(summary.aggregated_value)

            return total

        except stripe.StripeError as e:
            logger.error(
                "Failed to get meter event summary",
                extra={
                    "customer_id": customer_id,
                    "error": str(e),
                },
            )
            raise StripeClientError(f"Failed to get meter summary: {e}", e) from e

    # =========================================================================
    # Webhooks
    # =========================================================================

    def construct_webhook_event(
        self,
        payload: bytes,
        signature: str,
    ) -> stripe.Event:
        """Construct and verify a webhook event from Stripe.

        Args:
            payload: Raw request body
            signature: Stripe-Signature header value

        Returns:
            Verified Stripe Event object

        Raises:
            StripeClientError: If signature verification fails

        """
        if not self._settings.webhook_secret:
            raise StripeConfigurationError("Webhook secret not configured")

        try:
            return stripe.Webhook.construct_event(
                payload,
                signature,
                self._settings.webhook_secret,
            )
        except stripe.SignatureVerificationError as e:
            logger.warning(
                "Webhook signature verification failed",
                extra={"error": str(e)},
            )
            raise StripeClientError("Invalid webhook signature", e) from e
        except ValueError as e:
            logger.warning(
                "Invalid webhook payload",
                extra={"error": str(e)},
            )
            raise StripeClientError("Invalid webhook payload") from e

    def parse_subscription_update(self, subscription_data: dict[str, Any]) -> SubscriptionUpdate:
        """Parse subscription data from a webhook event into our DTO.

        Args:
            subscription_data: The 'data.object' from a subscription webhook event

        Returns:
            SubscriptionUpdate DTO

        """
        # Quantity lives on items.data[0].quantity, not the subscription root.
        # Stripe's Subscription object has no top-level "quantity" field.
        items_data = subscription_data.get("items", {}).get("data", [])
        quantity = items_data[0].get("quantity", 1) if items_data else 1

        return SubscriptionUpdate(
            stripe_subscription_id=subscription_data["id"],
            stripe_customer_id=subscription_data["customer"],
            status=subscription_data["status"],
            quantity=quantity,
            current_period_start=datetime.fromtimestamp(
                subscription_data["current_period_start"], tz=UTC
            ),
            current_period_end=datetime.fromtimestamp(
                subscription_data["current_period_end"], tz=UTC
            ),
            cancel_at_period_end=subscription_data.get("cancel_at_period_end", False),
            canceled_at=(
                datetime.fromtimestamp(subscription_data["canceled_at"], tz=UTC)
                if subscription_data.get("canceled_at")
                else None
            ),
        )
