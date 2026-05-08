"""FastAPI router for billing endpoints.

These endpoints handle:
- Customer portal access
- Usage queries
- Webhook processing

The router is designed to be mounted at /api/v1/billing.
"""

from __future__ import annotations

import json
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shu.api.dependencies import get_db
from shu.auth.models import User
from shu.auth.rbac import get_current_user, require_admin
from shu.billing.adapters import (
    UsageProviderImpl,
    create_cycle_rollover_callback,
    create_payment_failed_callback,
    create_payment_recovered_callback,
    create_subscription_persistence_callback,
    get_active_user_count,
    get_billing_config,
)
from shu.billing.config import BillingSettings, get_billing_settings_dependency
from shu.billing.enforcement import get_current_billing_state
from shu.billing.router_envelope import verify_router_envelope_dep
from shu.billing.schemas import WebhookEventResponse
from shu.billing.seat_service import (
    SeatMinimumError,
    SeatService,
    SeatServiceError,
    get_seat_service,
)
from shu.billing.service import BillingService, CustomerMismatchError
from shu.billing.stripe_client import StripeClient, StripeClientError
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
            detail="No billing customer configured. Set SHU_STRIPE_CUSTOMER_ID.",
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
)
async def get_subscription_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[BillingSettings, Depends(get_billing_settings_dependency)],
) -> JSONResponse:
    """Get current subscription status.

    Non-admin users receive quota fields only (user_count, user_limit,
    at_user_limit, user_limit_enforcement). Admin users additionally receive
    sensitive Stripe identifiers and billing period details.
    """
    billing_config = await get_billing_config(db)
    user_count = await get_active_user_count(db)
    enforcement = billing_config.get("user_limit_enforcement", "soft")
    subscription_id = billing_config.get("stripe_subscription_id")

    user_limit = 0
    target_quantity = 0
    stripe_client: StripeClient | None = None
    if subscription_id and settings.is_configured:
        try:
            stripe_client = StripeClient(settings)
            user_limit, target_quantity, _ = await stripe_client.get_subscription_seat_state(subscription_id)
        except StripeClientError as e:
            # Fail-closed to match check_user_limit. Surfacing the outage as
            # 502 keeps the frontend from rendering a "no limit" view that
            # would let admins exceed Stripe quantity while billing is down.
            logger.error("Failed to fetch live seat state from Stripe", extra={"error": str(e)})
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Billing provider unavailable",
            )

    state = await get_current_billing_state()

    payload: dict = {
        "user_count": user_count,
        "user_limit": user_limit,
        "target_quantity": target_quantity,
        "user_limit_enforcement": enforcement,
        "at_user_limit": user_count >= user_limit > 0,
        "payment_failed_at": state.payment_failed_at.isoformat() if state.payment_failed_at else None,
        "payment_grace_days": state.payment_grace_days,
        "grace_deadline": state.grace_deadline.isoformat() if state.grace_deadline else None,
        "service_paused": state.openrouter_key_disabled,
    }

    if user.can_manage_users():
        # Pull the active credit-grant total from Stripe for the Cost & Usage
        # dashboard's "Included Allowance" tile. Display-only — failing here
        # falls back to a client-side seats x $50 estimate, so don't 502 the
        # whole subscription endpoint over a non-critical field.
        included_usd_per_period: float | None = None
        customer_id = billing_config.get("stripe_customer_id")
        if customer_id and stripe_client is not None:
            try:
                included_usd_per_period = float(await stripe_client.get_active_credit_grant_total_usd(customer_id))
            except StripeClientError as e:
                logger.warning(
                    "Failed to fetch credit grants; allowance falls back to client-side estimate",
                    extra={"customer_id": customer_id, "error": str(e)},
                )

        # Pull the customer-billed markup ratio from the metered Price's
        # unit_amount_decimal. Same display-only contract as included_usd:
        # log + null on failure, frontend falls back to a constant.
        usage_markup_multiplier: float | None = None
        if subscription_id and stripe_client is not None:
            try:
                markup = await stripe_client.get_subscription_markup_multiplier(subscription_id)
                if markup is not None:
                    usage_markup_multiplier = float(markup)
            except StripeClientError as e:
                logger.warning(
                    "Failed to fetch usage markup; falls back to client-side constant",
                    extra={"subscription_id": subscription_id, "error": str(e)},
                )

        payload.update(
            {
                "stripe_customer_id": billing_config.get("stripe_customer_id"),
                "stripe_subscription_id": billing_config.get("stripe_subscription_id"),
                "subscription_status": billing_config.get("subscription_status", "pending"),
                "current_period_start": billing_config.get("current_period_start"),
                "current_period_end": billing_config.get("current_period_end"),
                "cancel_at_period_end": billing_config.get("cancel_at_period_end", False),
                "included_usd_per_period": included_usd_per_period,
                "usage_markup_multiplier": usage_markup_multiplier,
            }
        )

    return ShuResponse.success(payload)


# =============================================================================
# Usage Endpoints
# =============================================================================


@router.get(
    "/usage",
    summary="Get current usage",
    description="Get token usage for the current billing period.",
    dependencies=[Depends(require_admin)],
)
async def get_current_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JSONResponse:
    """Get usage for the current billing period.

    Returns an unknown-period response with empty totals when the billing
    period hasn't been populated yet (no subscription webhook received yet).
    Otherwise returns total tokens used, breakdown by model, and estimated
    cost for the active subscription period.
    """
    from datetime import datetime

    billing_config = await get_billing_config(db)
    period_start_str = billing_config.get("current_period_start")
    period_end_str = billing_config.get("current_period_end")

    if not (period_start_str and period_end_str):
        return ShuResponse.success(
            {
                "current_period_unknown": True,
                "period_start": None,
                "period_end": None,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_usd": 0.0,
                "by_model": [],
            }
        )

    period_start = datetime.fromisoformat(period_start_str)
    period_end = datetime.fromisoformat(period_end_str)
    usage_provider = UsageProviderImpl(db)
    summary = await usage_provider.get_usage_summary(period_start, period_end)

    # Convert Decimal to float at the API boundary — JSON has no Decimal type.
    # Display precision is fine; billing precision is preserved internally.
    return ShuResponse.success(
        {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_input_tokens": summary.total_input_tokens,
            "total_output_tokens": summary.total_output_tokens,
            "total_cost_usd": float(summary.total_cost_usd),
            "by_model": [
                {
                    "model_id": m.model_id,
                    "model_name": m.model_name,
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "cost_usd": float(m.cost_usd),
                    "request_count": m.request_count,
                }
                for m in summary.by_model.values()
            ],
        }
    )


# =============================================================================
# Seat management
# =============================================================================


@router.post(
    "/seats/cancel-release",
    summary="Cancel pending seat release",
    description="Wipe the pending Stripe downgrade and clear all user deactivation flags.",
    dependencies=[Depends(require_admin)],
)
async def cancel_pending_release(
    db: Annotated[AsyncSession, Depends(get_db)],
    seat_service: Annotated[SeatService | None, Depends(get_seat_service)],
) -> JSONResponse:
    """Undo all pending downgrade actions — both open-seat releases and user flags.

    Releases the Stripe subscription schedule and clears every user's
    ``deactivation_scheduled_at``. Returns the fresh ``UserLimitStatus``.
    """
    if seat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured",
        )
    try:
        status_result = await seat_service.cancel_pending_release(db)
    except StripeClientError as e:
        logger.error("Stripe error during cancel-release", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing provider error",
        )
    except SeatServiceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return ShuResponse.success(
        {
            "user_count": status_result.current_count,
            "user_limit": status_result.user_limit,
            "user_limit_enforcement": status_result.enforcement,
            "at_user_limit": status_result.at_limit,
        }
    )


@router.post(
    "/seats/release",
    summary="Release one open seat",
    description="Shrink Stripe seat quantity by one without touching user rows.",
    dependencies=[Depends(require_admin)],
)
async def release_open_seat(
    db: Annotated[AsyncSession, Depends(get_db)],
    seat_service: Annotated[SeatService | None, Depends(get_seat_service)],
) -> JSONResponse:
    """Schedule a one-seat downgrade at the next period end.

    Returns the fresh `UserLimitStatus` so the frontend can re-render the
    seat counter without a follow-up GET on `/billing/subscription`.
    """
    if seat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured",
        )
    try:
        status_result = await seat_service.release_open_seat(db)
    except SeatMinimumError as e:
        return ShuResponse.error(
            message=str(e),
            code="cannot_release_below_minimum",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except StripeClientError as e:
        logger.error("Stripe error during seat release", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing provider error",
        )
    except SeatServiceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return ShuResponse.success(
        {
            "user_count": status_result.current_count,
            "user_limit": status_result.user_limit,
            "user_limit_enforcement": status_result.enforcement,
            "at_user_limit": status_result.at_limit,
        }
    )


# =============================================================================
# Webhooks
# =============================================================================


@router.post(
    "/webhooks",
    summary="Router-forwarded Stripe webhook receiver",
    description=(
        "Receives Stripe webhook events forwarded from the Shu Control Plane. "
        "The router envelope (HMAC-SHA256 over timestamp + method + path + body, "
        "headers X-Shu-Router-Timestamp / X-Shu-Router-Signature) is verified "
        "via the verify_router_envelope_dep dependency before this handler runs."
    ),
    include_in_schema=False,  # Hide from OpenAPI docs
)
async def handle_webhook(
    body: Annotated[bytes, Depends(verify_router_envelope_dep)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[BillingService, Depends(get_billing_service)],
    seat_service: Annotated[SeatService | None, Depends(get_seat_service)],
) -> JSONResponse:
    """Process a router-forwarded Stripe webhook event.

    Authentication has already happened by the time this runs:
    1. The control plane verified the Stripe signature at its edge with its
       own SHU_CP_STRIPE_WEBHOOK_SECRET.
    2. verify_router_envelope_dep verified the HMAC envelope using this
       tenant's SHU_ROUTER_SHARED_SECRET.

    This handler parses the already-verified body as a Stripe event, applies
    the defense-in-depth customer-scope check, and dispatches. Handlers must
    be idempotent — the router will retry on tenant 5xx.
    """
    try:
        event_payload = json.loads(body)
    except json.JSONDecodeError as e:
        # The router forwards Stripe's body verbatim and Stripe always sends
        # JSON, so a decode failure here means either the router is broken or
        # we verified a non-Stripe payload. Either way, return 400 so the
        # router doesn't retry an unfixable request.
        logger.error("Forwarded webhook body is not JSON", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_json"},
        )

    # Construct a stripe.Event from the verified dict. No signature verification
    # — that happened upstream. Passing stripe.api_key mirrors the SDK internals
    # for any follow-up API calls made from the event context.
    event = stripe.Event.construct_from(event_payload, stripe.api_key)

    try:
        persist_subscription = await create_subscription_persistence_callback(db)
        on_payment_failed = await create_payment_failed_callback(db)
        on_payment_recovered = await create_payment_recovered_callback(db)
        # seat_service is only None when billing isn't configured. Webhooks
        # shouldn't reach an unconfigured tenant in practice, but guard so
        # the type matches reality and we don't NPE if the router is
        # ever wired to a half-provisioned instance.
        on_cycle_rollover = create_cycle_rollover_callback(db, seat_service) if seat_service is not None else None
        billing_config = await get_billing_config(db)
        expected_customer_id = billing_config.get("stripe_customer_id")

        _handled, event_type, event_id = await service.handle_webhook(
            event=event,
            persist_subscription=persist_subscription,
            on_payment_failed=on_payment_failed,
            on_payment_recovered=on_payment_recovered,
            on_cycle_rollover=on_cycle_rollover,
            expected_customer_id=expected_customer_id,
        )

        return ShuResponse.success(
            WebhookEventResponse(
                received=True,
                event_id=event_id,
                event_type=event_type,
            ).model_dump()
        )

    except CustomerMismatchError as e:
        # Defense-in-depth surfaced a router registry misconfiguration. Return
        # a structured 409 so the router (once its forwarder parses error
        # bodies) can log this as TENANT_CUSTOMER_MISMATCH rather than a
        # generic TENANT_ERROR. Stripe retries on 5xx, not 4xx, so 409 also
        # prevents Stripe from hammering the mismatched tenant while the
        # operator fixes the registry.
        logger.warning(
            "Rejecting forwarded webhook — customer mismatch",
            extra={"expected": e.expected, "received": e.received},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "customer_mismatch",
                "expected": e.expected,
                "received": e.received,
            },
        )
    except StripeClientError as e:
        logger.error(
            "Webhook handler error (Stripe SDK)",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "webhook_processing_failed"},
        )
    except Exception as e:
        logger.error(
            "Webhook handler error",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "webhook_processing_failed"},
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
    current_user: Annotated[User, Depends(get_current_user)],
) -> JSONResponse:
    """Get public billing configuration.

    Returns configuration that's safe to expose to the frontend,
    such as the Stripe publishable key. Admins additionally get a live
    ``validation_issues`` list — same shape as the startup log lines —
    so they can re-inspect after env-var changes without scraping logs.
    """
    payload: dict[str, object] = {
        "configured": settings.is_configured,
        "publishable_key": settings.publishable_key,
        "mode": settings.mode,
    }
    if current_user.can_manage_users():
        try:
            payload["validation_issues"] = settings.validate_configuration() if settings.is_configured else []
        except Exception:
            payload["validation_issues"] = []
    return ShuResponse.success(payload)
