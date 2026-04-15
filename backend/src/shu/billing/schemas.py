"""Pydantic schemas for billing API.

These schemas define the request/response models for billing endpoints
and internal data transfer objects.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# =============================================================================
# Subscription
# =============================================================================


class PortalSessionResponse(BaseModel):
    """Response containing Customer Portal URL."""

    url: str


# =============================================================================
# Webhooks
# =============================================================================


class WebhookEventResponse(BaseModel):
    """Response after processing a webhook event."""

    received: bool = True
    event_id: str | None = None
    event_type: str | None = None


# =============================================================================
# Internal DTOs
# =============================================================================


class UsageMeterEvent(BaseModel):
    """Data for reporting usage to Stripe Meters API."""

    # Event category — must match the event name configured on the Stripe meter
    event_name: str  # e.g., "usage_cost"

    # Stripe customer ID
    stripe_customer_id: str

    # Timestamp of the usage
    timestamp: int  # Unix timestamp

    # Usage value (cost in microdollars)
    value: int

    # Deterministic identifier for Stripe-side deduplication.
    # Two events with the same identifier within 24h are treated as duplicates,
    # protecting against double-counting on retries (timeouts, network blips).
    identifier: str

    # Additional context
    payload: dict[str, str] = Field(default_factory=dict)


class SubscriptionUpdate(BaseModel):
    """Internal DTO for subscription state changes from webhooks."""

    stripe_subscription_id: str
    stripe_customer_id: str
    status: str
    quantity: int
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False
    canceled_at: datetime | None = None
