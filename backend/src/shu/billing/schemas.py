"""Pydantic schemas for billing API.

These schemas define the request/response models for billing endpoints
and internal data transfer objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# =============================================================================
# Checkout & Subscription
# =============================================================================


class CheckoutSessionCreate(BaseModel):
    """Request to create a Stripe Checkout session."""

    # Number of users/seats for the subscription
    quantity: int = Field(ge=1, le=1000, description="Number of seats")

    # Optional: pre-fill customer email
    customer_email: str | None = None

    # Where to redirect after success/cancel
    success_url: str | None = None
    cancel_url: str | None = None

    # Optional metadata to attach to the subscription
    metadata: dict[str, str] | None = None


class CheckoutSessionResponse(BaseModel):
    """Response containing Checkout session URL."""

    session_id: str
    url: str


class PortalSessionResponse(BaseModel):
    """Response containing Customer Portal URL."""

    url: str


class SubscriptionStatus(BaseModel):
    """Current subscription status for a customer."""

    customer_id: UUID
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    status: Literal["pending", "active", "past_due", "canceled", "unpaid", "trialing"]
    current_period_start: datetime | None
    current_period_end: datetime | None
    quantity: int  # number of seats
    cancel_at_period_end: bool = False


# =============================================================================
# Usage & Billing
# =============================================================================


class ModelUsageSummary(BaseModel):
    """Usage summary for a single model."""

    model_id: str
    model_name: str | None = None  # Human-readable name
    input_tokens: int
    output_tokens: int
    cost_usd: float
    request_count: int


class TypeUsageSummary(BaseModel):
    """Usage summary by usage type."""

    usage_type: str  # 'chat', 'profiling', 'side_call'
    input_tokens: int
    output_tokens: int
    cost_usd: float
    request_count: int


class UsageSummaryResponse(BaseModel):
    """Aggregated usage data for display."""

    period_start: datetime
    period_end: datetime

    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float

    # Breakdown by model
    by_model: list[ModelUsageSummary]

    # Breakdown by type (chat, profiling, etc.)
    by_type: list[TypeUsageSummary]


class CurrentUsageResponse(BaseModel):
    """Current billing period usage."""

    period_start: datetime
    period_end: datetime

    # Token usage
    total_tokens_used: int
    included_tokens: int  # from subscription
    overage_tokens: int

    # Cost
    estimated_overage_cost_usd: float

    # For display
    usage_percentage: float  # percent of included tokens used


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


class StripeCustomerData(BaseModel):
    """Data needed to create/update a Stripe customer."""

    email: str
    name: str
    metadata: dict[str, str] = Field(default_factory=dict)


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
