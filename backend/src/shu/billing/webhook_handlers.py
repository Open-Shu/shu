"""Stripe webhook event handlers.

Each handler processes a specific Stripe event type and updates internal state.
Handlers must be idempotent - Stripe may deliver events multiple times.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import stripe

from shu.billing.schemas import SubscriptionUpdate
from shu.billing.stripe_client import StripeClient
from shu.core.logging import get_logger

logger = get_logger(__name__)


# Type alias for the callback that persists subscription changes
# This is injected by the service layer to decouple from the data layer
SubscriptionCallback = Callable[[SubscriptionUpdate], Any]
CustomerCallback = Callable[[str, str], Any]  # (stripe_customer_id, email)
PaymentFailedCallback = Callable[[str, str, str], Any]  # (stripe_customer_id, subscription_id, invoice_id)


class WebhookHandler(ABC):
    """Base class for webhook event handlers."""

    event_type: str  # The Stripe event type this handler processes

    @abstractmethod
    async def handle(self, event: stripe.Event, **callbacks: Any) -> None:
        """Process the webhook event.

        Args:
            event: The verified Stripe event
            **callbacks: Callback functions for persisting changes

        """
        pass


class CustomerCreatedHandler(WebhookHandler):
    """Handle customer.created events.

    This fires when a new customer is created via Checkout or API.
    We use this to link the Stripe customer to our organization.
    """

    event_type = "customer.created"

    async def handle(
        self,
        event: stripe.Event,
        on_customer_created: CustomerCallback | None = None,
        **kwargs: Any,
    ) -> None:
        customer = event.data.object
        customer_id = customer.get("id")
        email = customer.get("email")

        logger.info(
            "Processing customer.created",
            extra={"stripe_customer_id": customer_id, "email": email},
        )

        if on_customer_created and email:
            await on_customer_created(customer_id, email)


class SubscriptionCreatedHandler(WebhookHandler):
    """Handle customer.subscription.created events.

    This fires when a new subscription is created. We need to:
    1. Link the subscription to our organization
    2. Trigger provisioning if needed
    """

    event_type = "customer.subscription.created"

    def __init__(self, stripe_client: StripeClient) -> None:
        self._client = stripe_client

    async def handle(
        self,
        event: stripe.Event,
        on_subscription_update: SubscriptionCallback | None = None,
        **kwargs: Any,
    ) -> None:
        subscription_data = event.data.object
        update = self._client.parse_subscription_update(subscription_data)

        logger.info(
            "Processing subscription.created",
            extra={
                "subscription_id": update.stripe_subscription_id,
                "customer_id": update.stripe_customer_id,
                "status": update.status,
                "quantity": update.quantity,
            },
        )

        if on_subscription_update:
            await on_subscription_update(update)


class SubscriptionUpdatedHandler(WebhookHandler):
    """Handle customer.subscription.updated events.

    This fires on any subscription change:
    - Status changes (active, past_due, canceled)
    - Quantity changes
    - Plan changes
    - Cancellation scheduled
    """

    event_type = "customer.subscription.updated"

    def __init__(self, stripe_client: StripeClient) -> None:
        self._client = stripe_client

    async def handle(
        self,
        event: stripe.Event,
        on_subscription_update: SubscriptionCallback | None = None,
        **kwargs: Any,
    ) -> None:
        subscription_data = event.data.object
        update = self._client.parse_subscription_update(subscription_data)

        # Log what changed (previous_attributes shows the old values)
        previous = event.data.get("previous_attributes", {})
        changes = list(previous.keys()) if previous else ["unknown"]

        logger.info(
            "Processing subscription.updated",
            extra={
                "subscription_id": update.stripe_subscription_id,
                "status": update.status,
                "quantity": update.quantity,
                "changed_fields": changes,
                "cancel_at_period_end": update.cancel_at_period_end,
            },
        )

        if on_subscription_update:
            await on_subscription_update(update)


class SubscriptionDeletedHandler(WebhookHandler):
    """Handle customer.subscription.deleted events.

    This fires when a subscription is fully canceled (not just scheduled).
    We need to update status and potentially deprovision/archive the tenant.
    """

    event_type = "customer.subscription.deleted"

    def __init__(self, stripe_client: StripeClient) -> None:
        self._client = stripe_client

    async def handle(
        self,
        event: stripe.Event,
        on_subscription_update: SubscriptionCallback | None = None,
        **kwargs: Any,
    ) -> None:
        subscription_data = event.data.object
        update = self._client.parse_subscription_update(subscription_data)

        logger.info(
            "Processing subscription.deleted",
            extra={
                "subscription_id": update.stripe_subscription_id,
                "customer_id": update.stripe_customer_id,
            },
        )

        if on_subscription_update:
            await on_subscription_update(update)


class InvoicePaidHandler(WebhookHandler):
    """Handle invoice.paid events.

    This confirms successful payment. For usage-based billing, this is when
    we know the customer has paid for their usage.
    """

    event_type = "invoice.paid"

    async def handle(self, event: stripe.Event, **kwargs: Any) -> None:
        invoice = event.data.object
        customer_id = invoice.get("customer")
        subscription_id = invoice.get("subscription")
        amount_paid = invoice.get("amount_paid", 0) / 100  # cents to dollars

        logger.info(
            "Processing invoice.paid",
            extra={
                "invoice_id": invoice.get("id"),
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "amount_paid": amount_paid,
            },
        )

        # Invoice paid - no action needed unless we want to send a receipt
        # Stripe handles receipts automatically if configured


class InvoicePaymentFailedHandler(WebhookHandler):
    """Handle invoice.payment_failed events.

    This indicates payment failure. We should:
    1. Log the failure
    2. Notify the customer (optional - Stripe has built-in dunning)
    3. Eventually suspend service if payment isn't recovered
    """

    event_type = "invoice.payment_failed"

    async def handle(
        self,
        event: stripe.Event,
        on_payment_failed: PaymentFailedCallback | None = None,
        **kwargs: Any,
    ) -> None:
        invoice = event.data.object
        customer_id = invoice.get("customer")
        subscription_id = invoice.get("subscription")
        invoice_id = invoice.get("id")
        attempt_count = invoice.get("attempt_count", 0)

        logger.warning(
            "Processing invoice.payment_failed",
            extra={
                "invoice_id": invoice_id,
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "attempt_count": attempt_count,
            },
        )

        if on_payment_failed and customer_id and subscription_id and invoice_id:
            await on_payment_failed(customer_id, subscription_id, invoice_id)


class CheckoutSessionCompletedHandler(WebhookHandler):
    """Handle checkout.session.completed events.

    This fires when a customer completes Checkout. For subscription mode,
    the subscription is already created, but we can use this to:
    1. Link the organization to Stripe customer/subscription
    2. Trigger any welcome/onboarding flows
    """

    event_type = "checkout.session.completed"

    async def handle(
        self,
        event: stripe.Event,
        on_checkout_completed: Callable[[dict[str, Any]], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        session = event.data.object
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        metadata = session.get("metadata", {})

        logger.info(
            "Processing checkout.session.completed",
            extra={
                "session_id": session.get("id"),
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "mode": session.get("mode"),
            },
        )

        if on_checkout_completed:
            await on_checkout_completed({
                "session_id": session.get("id"),
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "customer_email": session.get("customer_email"),
                "metadata": metadata,
            })


class WebhookDispatcher:
    """Routes webhook events to appropriate handlers.

    Usage:
        dispatcher = WebhookDispatcher(stripe_client)
        await dispatcher.dispatch(event, on_subscription_update=my_callback)
    """

    def __init__(self, stripe_client: StripeClient) -> None:
        self._client = stripe_client
        self._handlers: dict[str, WebhookHandler] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register all webhook handlers."""
        handlers: list[WebhookHandler] = [
            CustomerCreatedHandler(),
            SubscriptionCreatedHandler(self._client),
            SubscriptionUpdatedHandler(self._client),
            SubscriptionDeletedHandler(self._client),
            InvoicePaidHandler(),
            InvoicePaymentFailedHandler(),
            CheckoutSessionCompletedHandler(),
        ]
        for handler in handlers:
            self._handlers[handler.event_type] = handler

    async def dispatch(self, event: stripe.Event, **callbacks: Any) -> bool:
        """Dispatch an event to its handler.

        Args:
            event: Verified Stripe event
            **callbacks: Callback functions for the handlers

        Returns:
            True if event was handled, False if no handler exists

        """
        event_type = event.type
        handler = self._handlers.get(event_type)

        if not handler:
            logger.debug(
                "No handler for webhook event type",
                extra={"event_type": event_type, "event_id": event.id},
            )
            return False

        try:
            await handler.handle(event, **callbacks)
            return True
        except Exception as e:
            logger.error(
                "Webhook handler failed",
                extra={
                    "event_type": event_type,
                    "event_id": event.id,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Re-raise to trigger Stripe retry
            raise

    @property
    def supported_events(self) -> list[str]:
        """List of event types this dispatcher can handle."""
        return list(self._handlers.keys())
