"""Stripe webhook event handlers.

Each handler processes a specific Stripe event type and updates internal state.
Handlers must be idempotent - Stripe may deliver events multiple times.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Protocol

import stripe

from shu.billing.schemas import SubscriptionUpdate
from shu.billing.stripe_client import StripeClient
from shu.core.logging import get_logger

logger = get_logger(__name__)


class SubscriptionCallback(Protocol):
    """Callback injected by the service layer to persist subscription changes.

    Handlers call it with the parsed SubscriptionUpdate. The service layer
    wraps the underlying persist function in a closure that captures the
    stripe_event_id from the outer webhook event, so handlers never need to
    pass it directly.
    """

    async def __call__(self, update: SubscriptionUpdate) -> None:
        """Persist a subscription state change."""
        ...


# (stripe_customer_id, subscription_id, invoice_id, stripe_event_id)
PaymentFailedCallback = Callable[[str, str, str, str | None], Any]
PaymentRecoveredCallback = Callable[[str, str, str, str | None], Any]
# (stripe_customer_id, subscription_id, invoice_id, stripe_event_id, billing_reason)
CycleRolloverCallback = Callable[[str, str, str, str, str | None], Any]


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

    Confirms successful payment and clears any outstanding payment failure
    marker so grace-period enforcement reflects the recovered account status.
    """

    event_type = "invoice.paid"

    async def handle(
        self,
        event: stripe.Event,
        on_payment_recovered: PaymentRecoveredCallback | None = None,
        on_cycle_rollover: CycleRolloverCallback | None = None,
        **kwargs: Any,
    ) -> None:
        invoice = event.data.object
        customer_id = invoice.get("customer")
        subscription_id = invoice.get("subscription")
        invoice_id = invoice.get("id")
        billing_reason = invoice.get("billing_reason")
        amount_paid = invoice.get("amount_paid", 0) / 100  # cents to dollars

        logger.info(
            "Processing invoice.paid",
            extra={
                "invoice_id": invoice_id,
                "customer_id": customer_id,
                "subscription_id": subscription_id,
                "billing_reason": billing_reason,
                "amount_paid": amount_paid,
            },
        )

        if on_payment_recovered and customer_id and subscription_id and invoice_id:
            await on_payment_recovered(customer_id, subscription_id, invoice_id, event.id)

        if on_cycle_rollover and customer_id and subscription_id and invoice_id:
            await on_cycle_rollover(customer_id, subscription_id, invoice_id, event.id, billing_reason)


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
            await on_payment_failed(customer_id, subscription_id, invoice_id, event.id)


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
            SubscriptionCreatedHandler(self._client),
            SubscriptionUpdatedHandler(self._client),
            SubscriptionDeletedHandler(self._client),
            InvoicePaidHandler(),
            InvoicePaymentFailedHandler(),
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
