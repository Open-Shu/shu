"""Stripe webhook event handlers.

After SHU-774 the tenant no longer persists subscription / payment state from
Stripe — CP is the source of truth and the tenant reads from the CP wire on
its poll. The only side-effect the tenant still drives off a Stripe event is
the seat rollover at cycle boundary (`invoice.paid` with
`billing_reason == "subscription_cycle"`). Everything else is log-only and
handled implicitly by Stripe events we don't subscribe to.

Handlers must be idempotent — Stripe may deliver events multiple times.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import stripe

from shu.core.logging import get_logger

logger = get_logger(__name__)


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


def _extract_invoice_subscription_id(invoice: Any) -> str | None:
    """Pull the subscription id off an invoice across API versions.

    API 2026-03-25.dahlia removed the top-level ``invoice.subscription`` field
    and moved the reference under ``invoice.parent.subscription_details.subscription``.
    Fall back to the legacy field for older payloads (and Stripe CLI fixtures
    pinned to earlier versions).
    """
    parent = invoice.get("parent") if hasattr(invoice, "get") else None
    if parent and parent.get("type") == "subscription_details":
        details = parent.get("subscription_details") or {}
        sub = details.get("subscription")
        if sub:
            return sub
    return invoice.get("subscription")


class InvoicePaidHandler(WebhookHandler):
    """Handle invoice.paid events for the cycle-rollover seat reconciliation.

    Other consequences of an invoice paying (clearing payment-failure state,
    confirming receipts) live in CP now — the tenant only cares about the
    cycle boundary because that's when scheduled seat releases land.
    """

    event_type = "invoice.paid"

    async def handle(
        self,
        event: stripe.Event,
        on_cycle_rollover: CycleRolloverCallback | None = None,
        **kwargs: Any,
    ) -> None:
        invoice = event.data.object
        customer_id = invoice.get("customer")
        subscription_id = _extract_invoice_subscription_id(invoice)
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

        if on_cycle_rollover and customer_id and subscription_id and invoice_id:
            await on_cycle_rollover(customer_id, subscription_id, invoice_id, event.id, billing_reason)


class WebhookDispatcher:
    """Routes webhook events to appropriate handlers.

    Usage:
        dispatcher = WebhookDispatcher()
        await dispatcher.dispatch(event, on_cycle_rollover=my_callback)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, WebhookHandler] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register all webhook handlers.

        Only `invoice.paid` is wired in the post-SHU-774 tenant — every other
        Stripe event the router forwards either has no tenant-side
        consequence or is handled entirely by CP. The dispatcher returns
        `False` for unhandled types so the route still logs and 200s back to
        the router (idempotent no-op) without triggering retries.
        """
        handlers: list[WebhookHandler] = [
            InvoicePaidHandler(),
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
