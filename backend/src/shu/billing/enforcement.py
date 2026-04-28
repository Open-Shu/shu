"""User limit enforcement for per-seat billing.

Checks whether the current user count is at or over the subscription
limit and returns a status object the caller can act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.adapters import get_active_user_count, get_billing_config
from shu.billing.stripe_client import StripeClient
from shu.core.logging import get_logger

logger = get_logger(__name__)


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
        from shu.billing.config import get_billing_settings

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
        from shu.billing.state_service import BillingStateService

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
