"""User limit enforcement for per-seat billing.

Checks whether the current user count is at or over the subscription
limit and returns a status object the caller can act on.

Also hosts the SHU-703 subscription-active gate consumed by every
billable chokepoint (OCR, embedding, chat, KB upload). Keeping the
two helpers in one module mirrors the "billing enforcement" boundary
in the design doc — there is no per-chokepoint policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.adapters import (
    UsageProviderImpl,
    get_active_user_count,
    get_billing_config,
)
from shu.billing.billing_state_cache import get_billing_state_cache
from shu.billing.config import get_billing_settings
from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.entitlements import EntitlementDeniedError
from shu.billing.markup import resolve_markup
from shu.billing.state_service import BillingStateService
from shu.billing.stripe_client import StripeClient
from shu.core.database import get_async_session_local
from shu.core.exceptions import ShuException
from shu.core.logging import get_logger

logger = get_logger(__name__)


class SubscriptionInactiveError(ShuException):
    """Raised when CP has disabled the OpenRouter key (post-grace lockout).

    Carries the wire payload the frontend banner consumes — `grace_deadline`
    is informational here (grace is already over by the time we raise),
    but including it lets the banner render "service paused on {date}"
    without a follow-up request.
    """

    def __init__(
        self,
        *,
        payment_failed_at: datetime | None,
        grace_deadline: datetime | None,
    ) -> None:
        super().__init__(
            message="Subscription is inactive — service paused.",
            error_code="subscription_inactive",
            status_code=402,
            details={
                "payment_failed_at": payment_failed_at.isoformat() if payment_failed_at else None,
                "grace_deadline": grace_deadline.isoformat() if grace_deadline else None,
            },
        )


class TrialCapExhaustedError(ShuException):
    """Raised when a trial tenant has spent through their grant pool.

    Distinct from `SubscriptionInactiveError` so the frontend can render
    the trial-exhausted surface (Upgrade now / Cancel trial) instead of
    the payment-failure surface (Update payment method).
    """

    def __init__(
        self,
        *,
        trial_deadline: datetime | None,
        total_grant_amount: Decimal,
    ) -> None:
        super().__init__(
            message="Trial usage budget exhausted.",
            error_code="trial_usage_exhausted",
            status_code=402,
            details={
                "trial_deadline": trial_deadline.isoformat() if trial_deadline else None,
                "total_grant_amount": str(total_grant_amount),
            },
        )


async def get_current_billing_state() -> BillingState:
    """Return the latest cached CP billing state, or HEALTHY_DEFAULT.

    HEALTHY_DEFAULT covers two distinct cases by design:
    - Self-hosted / dev: the cache singleton was never populated because
      CP isn't configured. Enforcement is a no-op.
    - Cold-start with CP unreachable: SHU-743 fail-open behavior — we
      prefer letting traffic through over locking customers out on a
      transient outage. The OpenRouter side gates chat/embed cost
      independently, so the leak surface is bounded.
    """
    cache = get_billing_state_cache()
    if cache is None:
        return HEALTHY_DEFAULT
    return await cache.get()


async def assert_subscription_active() -> None:
    """Gate every billable chokepoint on payment-status AND trial-cap.

    Two independent failure modes share this single entry point so call
    sites (chat / embed / OCR / KB upload / worker handlers) get both
    checks without per-site wiring. Trialing subscriptions are still
    "active" in Stripe's sense; treating cap-exhaustion as another mode
    of "not active right now" keeps the assertion semantically honest.

    Precedence: payment failure raises first. A `past_due` tenant who
    happens to be trialing should see the payment-failure surface (it's
    the binding gate), not the trial-exhausted one.
    """
    cache = get_billing_state_cache()

    # Self-hosted / dev: cache singleton missing → no enforcement at all.
    # Without this guard, `HEALTHY_DEFAULT.is_trial=True` (the cold-start
    # fail-closed posture) would route self-hosted dev tenants into the
    # trial-cap branch.
    if cache is None:
        return

    state = await cache.get()

    if state.openrouter_key_disabled:
        raise SubscriptionInactiveError(
            payment_failed_at=state.payment_failed_at,
            grace_deadline=state.grace_deadline,
        )

    # Open the session once and reuse for both the cancel-status check and
    # (if applicable) the trial-cap DB query. The cancel-status read is
    # unconditional because the cache alone can't tell us a cancel just
    # happened: Stripe flips sub status to `canceled` synchronously on our
    # cancel call → cache `is_trial=False` → enforcement would fall through
    # to `return` before CP's webhook lands and disables the OR key. Local
    # `subscription_status` is written inline by the cancel router and by
    # the forwarded webhook, so it leads `openrouter_key_disabled` by one
    # CP round-trip.
    # TODO: lift `is_cancelled` (or full status) onto `BillingStateResponse`
    # so this DB read can move back behind the `is_trial` guard. In fact, we should
    # move all the fields that need to be loaded from the billing_row. That way
    # they are cached and we reduce DB load.
    session_local = get_async_session_local()
    async with session_local() as db:
        billing_row = await BillingStateService.get(db)

        if billing_row is not None and billing_row.subscription_status == "canceled":
            raise SubscriptionInactiveError(
                payment_failed_at=state.payment_failed_at,
                grace_deadline=state.grace_deadline,
            )

        if not state.is_trial:
            return

        # Trial-cap path: precise per-period DB query rather than reading
        # `state.remaining_grant_amount` from the cache. Cache is up to one
        # TTL stale (default 500s) — too loose for trial-spend enforcement
        # where minutes of overage are real cost the company eats.
        if billing_row is None or billing_row.current_period_start is None:
            # Fail-closed on missing period info: silent bypass would
            # let unbounded trial spend through on a data anomaly.
            raise TrialCapExhaustedError(
                trial_deadline=state.trial_deadline,
                total_grant_amount=state.total_grant_amount,
            )
        summary = await UsageProviderImpl(db).get_usage_summary(
            billing_row.current_period_start,
            datetime.now(UTC),
        )
        # `total_cost_usd` is raw provider cost; `total_grant_amount` is
        # customer-billed (from CP/Stripe credit grant). Apply the markup
        # before comparing so the trial-cap triggers on the billed dollar
        # the customer would have been charged, not the raw provider dollar.
        billed_cost = summary.total_cost_usd * resolve_markup(state)
        if billed_cost >= state.total_grant_amount:
            raise TrialCapExhaustedError(
                trial_deadline=state.trial_deadline,
                total_grant_amount=state.total_grant_amount,
            )


@dataclass(frozen=True)
class UserLimitStatus:
    """Result of a user limit check.

    Attributes:
        enforcement: "none" (no billing/limit), "soft" (warn), or "hard" (block).
        at_limit: Whether current_count >= user_limit.
        current_count: Number of users in the database.
        user_limit: Maximum users allowed by subscription (0 = unlimited).

    """

    enforcement: str  # "none" | "soft" | "hard"
    at_limit: bool
    current_count: int
    user_limit: int


_NO_LIMIT = UserLimitStatus(enforcement="none", at_limit=False, current_count=0, user_limit=0)


async def check_user_limit(
    db: AsyncSession,
    stripe_client: StripeClient | None = None,
) -> UserLimitStatus:
    """Check whether the instance is at or over its subscription user limit.

    Reads live seat qty from Stripe (source of truth) rather than a local
    cache. Returns UserLimitStatus with enforcement="none" when:
    - No billing config exists (self-hosted, no Stripe)
    - No subscription is linked
    - Subscription quantity is 0 (treat as unlimited)

    Raises ``StripeClientError`` when Stripe is unreachable. Callers must
    surface this as a 5xx so admins retry once Stripe recovers — silently
    treating an outage as "no limit" would let admins create unlimited
    seats while billing is offline, which throws the count out of whack
    once Stripe comes back.

    Callers decide what to do with the result:
    - enforcement="hard" + at_limit=True → block user creation
    - enforcement="none" or "soft" → no limit, allow creation

    ``stripe_client`` is optional — when omitted, a fresh client is built
    from the singleton settings. Callers that already have a client (e.g.
    ``SeatService``) should pass theirs to skip the construction.
    """
    billing_config = await get_billing_config(db)

    subscription_id = billing_config.get("stripe_subscription_id") if billing_config else None
    if not subscription_id:
        return _NO_LIMIT

    if stripe_client is None:
        settings = get_billing_settings()
        if not settings.is_configured:
            return _NO_LIMIT
        stripe_client = StripeClient(settings)

    # Let StripeClientError propagate — fail-closed on outage so admins
    # don't accidentally exceed Stripe's seat count while billing is down.
    user_limit, _, _ = await stripe_client.get_subscription_seat_state(subscription_id)

    if user_limit == 0:
        return _NO_LIMIT

    # `soft` is a legal legacy column value with no behavior distinct from
    # `hard` today; collapse to `none` so callers don't need to special-case.
    enforcement = billing_config.get("user_limit_enforcement", "soft")
    if enforcement == "soft":
        enforcement = "none"

    if enforcement == "hard":
        # Serialise concurrent user-creation requests at the DB level.
        # Acquiring billing_state FOR UPDATE means the second request blocks
        # here until the first has committed its INSERT, so both cannot pass
        # the limit check simultaneously with the same user count.
        await BillingStateService.get_for_update(db)

    current_count = await get_active_user_count(db)
    at_limit = current_count >= user_limit

    result = UserLimitStatus(
        enforcement=enforcement,
        at_limit=at_limit,
        current_count=current_count,
        user_limit=user_limit,
    )
    logger.debug(
        "User limit check",
        extra={"enforcement": enforcement, "at_limit": at_limit, "count": current_count, "limit": user_limit},
    )
    return result


async def assert_entitlement(key: str) -> None:
    """Raise `EntitlementDeniedError` if the cached entitlement is not True.

    Self-hosted / dev (cache singleton missing) bypasses enforcement —
    matches `assert_subscription_active`'s bypass so dev environments
    aren't gated on a CP that isn't configured. Unknown keys also fail
    the check: `getattr(state.entitlements, "<typo>", False)` returns
    False, defending against route declarations that reference a key
    not on the `EntitlementSet`.
    """
    cache = get_billing_state_cache()
    if cache is None:
        return
    state = await cache.get()
    if getattr(state.entitlements, key, False) is not True:
        raise EntitlementDeniedError(key)


def require_entitlement(key: str):
    """FastAPI dependency factory matching the `require_admin` pattern.

    Usage: `Depends(require_entitlement("plugins"))` on a route declaration.
    The returned dependency runs `assert_entitlement(key)` before the route
    handler executes; entitlement denial raises before the route body runs.
    """

    async def dep() -> None:
        await assert_entitlement(key)

    return dep
