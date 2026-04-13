"""FastAPI router for billing endpoints.

These endpoints handle:
- Checkout session creation
- Customer portal access
- Usage queries
- Webhook processing

The router is designed to be mounted at /api/v1/billing.
"""

from __future__ import annotations

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shu.api.dependencies import get_db
from shu.auth.rbac import get_current_user, require_admin
from shu.billing.adapters import (
    UsageProviderImpl,
    create_customer_link_callback,
    create_subscription_persistence_callback,
    get_billing_config,
    get_user_count,
)
from shu.billing.config import BillingSettings, get_billing_settings_dependency
from shu.billing.schemas import CheckoutSessionCreate, WebhookEventResponse
from shu.billing.service import BillingService
from shu.billing.stripe_client import StripeClientError, StripeConfigurationError
from shu.core.logging import get_logger
from shu.core.response import ShuResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# =============================================================================
# Dependencies
# =============================================================================


def get_billing_service(
    settings: Annotated[BillingSettings, Depends(get_billing_settings_dependency)],
) -> BillingService:
    """Dependency to get the billing service."""
    if not settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured",
        )
    return BillingService(settings)


# =============================================================================
# Checkout Endpoints
# =============================================================================


@router.post(
    "/checkout",
    summary="Create checkout session",
    description="Create a Stripe Checkout session for new subscription signup.",
    dependencies=[Depends(require_admin)],
)
async def create_checkout_session(
    request: CheckoutSessionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BillingService, Depends(get_billing_service)],
) -> JSONResponse:
    """Create a Stripe Checkout session.

    This redirects the user to Stripe's hosted checkout page.
    After completion, they're redirected back to success_url or cancel_url.
    """
    try:
        billing_config = await get_billing_config(db)
        session = await service.create_checkout_session(
            request=request,
            customer_email=request.customer_email,
            stripe_customer_id=billing_config.get("stripe_customer_id"),
        )

        # Record this session's ID so the webhook handler can verify that
        # a future checkout.session.completed event belongs to us (multi-instance
        # safety: another tenant's checkout on the same Stripe account must not
        # bind this instance to their customer).
        from shu.billing.state_service import BillingStateService

        await BillingStateService.update(
            db,
            updates={"pending_checkout_session_id": session.session_id},
            source="api:checkout_session_create",
        )

        return ShuResponse.success(session.model_dump())
    except StripeConfigurationError as e:
        logger.error("Stripe configuration error", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except StripeClientError as e:
        logger.error("Stripe API error", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create checkout session",
        )


# =============================================================================
# Portal Endpoints
# =============================================================================


@router.get(
    "/portal",
    summary="Get customer portal URL",
    description="Get a URL to Stripe's Customer Portal for billing management.",
    dependencies=[Depends(require_admin)],
)
async def get_portal_session(
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BillingService, Depends(get_billing_service)],
    return_url: str | None = None,
) -> JSONResponse:
    """Get a Customer Portal session URL.

    The portal allows customers to:
    - Update payment methods
    - View invoices
    - Cancel subscription
    - Update billing information
    """
    billing_config = await get_billing_config(db)
    stripe_customer_id = billing_config.get("stripe_customer_id")

    if not stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account linked. Complete checkout first.",
        )

    try:
        session = await service.create_portal_session(stripe_customer_id, return_url)
        return ShuResponse.success(session.model_dump())
    except StripeClientError as e:
        logger.error("Stripe API error", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create portal session",
        )


# =============================================================================
# Subscription Status
# =============================================================================


@router.get(
    "/subscription",
    summary="Get subscription status",
    description="Get the current subscription status.",
    dependencies=[Depends(get_current_user)],
)
async def get_subscription_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Get current subscription status.

    Returns subscription details including:
    - Status (active, past_due, canceled, etc.)
    - Current billing period
    - User count
    """
    billing_config = await get_billing_config(db)
    user_count = await get_user_count(db)
    user_limit = billing_config.get("quantity", 0)
    enforcement = billing_config.get("user_limit_enforcement", "soft")

    return ShuResponse.success({
        "stripe_customer_id": billing_config.get("stripe_customer_id"),
        "stripe_subscription_id": billing_config.get("stripe_subscription_id"),
        "subscription_status": billing_config.get("subscription_status", "pending"),
        "current_period_start": billing_config.get("current_period_start"),
        "current_period_end": billing_config.get("current_period_end"),
        "user_count": user_count,
        "user_limit": user_limit,
        "user_limit_enforcement": enforcement,
        "at_user_limit": user_count >= user_limit > 0,
        "cancel_at_period_end": billing_config.get("cancel_at_period_end", False),
    })


# =============================================================================
# Usage Endpoints
# =============================================================================


@router.get(
    "/usage",
    summary="Get current usage",
    description="Get token usage for the current billing period.",
    dependencies=[Depends(get_current_user)],
)
async def get_current_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Get usage for the current billing period.

    Returns:
    - Total tokens used
    - Breakdown by model
    - Estimated cost

    """
    from datetime import datetime

    billing_config = await get_billing_config(db)
    usage_provider = UsageProviderImpl(db)

    # Determine billing period
    period_start_str = billing_config.get("current_period_start")
    period_end_str = billing_config.get("current_period_end")

    if period_start_str and period_end_str:
        period_start = datetime.fromisoformat(period_start_str)
        period_end = datetime.fromisoformat(period_end_str)
    else:
        # Default to current month
        now = datetime.now(UTC)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            period_end = period_start.replace(year=now.year + 1, month=1)
        else:
            period_end = period_start.replace(month=now.month + 1)

    summary = await usage_provider.get_usage_summary(period_start, period_end)

    # Convert Decimal to float at the API boundary — JSON has no Decimal type.
    # Display precision is fine; billing precision is preserved internally.
    return ShuResponse.success({
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_input_tokens": summary.total_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_cost_usd": float(summary.total_cost_usd),
        "by_model": [
            {
                "model_id": m.model_id,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "cost_usd": float(m.cost_usd),
                "request_count": m.request_count,
            }
            for m in summary.by_model.values()
        ],
    })


# =============================================================================
# Webhooks
# =============================================================================


@router.post(
    "/webhooks",
    summary="Stripe webhook receiver",
    description="Receives and processes Stripe webhook events.",
    include_in_schema=False,  # Hide from OpenAPI docs
)
async def handle_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BillingService, Depends(get_billing_service)],
    stripe_signature: Annotated[str, Header(alias="Stripe-Signature")],
) -> JSONResponse:
    """Process Stripe webhook events.

    This endpoint:
    1. Verifies the webhook signature
    2. Dispatches to appropriate handler
    3. Updates billing_state under a row-level lock

    Stripe will retry failed webhooks, so handlers must be idempotent.
    """
    payload = await request.body()

    try:
        persist_subscription = await create_subscription_persistence_callback(db)
        persist_customer_link = await create_customer_link_callback(db)

        # Pass current customer_id so the service can reject events
        # for other customers (multi-instance safety). Also pass the
        # pending checkout session ID so a fresh instance only accepts
        # the checkout.session.completed it initiated.
        billing_config = await get_billing_config(db)
        expected_customer_id = billing_config.get("stripe_customer_id")
        pending_checkout_session_id = billing_config.get("pending_checkout_session_id")

        handled, event_type, event_id = await service.handle_webhook(
            payload=payload,
            signature=stripe_signature,
            persist_subscription=persist_subscription,
            persist_customer_link=persist_customer_link,
            expected_customer_id=expected_customer_id,
            pending_checkout_session_id=pending_checkout_session_id,
        )

        return ShuResponse.success(
            WebhookEventResponse(
                received=True,
                event_id=event_id,
                event_type=event_type,
            ).model_dump()
        )

    except StripeClientError as e:
        logger.warning(
            "Webhook verification failed",
            extra={"error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )
    except Exception as e:
        logger.error(
            "Webhook handler error",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        )


# =============================================================================
# Health/Config Endpoints
# =============================================================================


@router.get(
    "/config",
    summary="Get billing configuration",
    description="Get public billing configuration (publishable key, etc.).",
)
async def get_billing_config_endpoint(
    settings: Annotated[BillingSettings, Depends(get_billing_settings_dependency)],
) -> JSONResponse:
    """Get public billing configuration.

    Returns configuration that's safe to expose to the frontend,
    such as the Stripe publishable key.
    """
    return ShuResponse.success({
        "configured": settings.is_configured,
        "publishable_key": settings.publishable_key,
        "mode": settings.mode,
    })
