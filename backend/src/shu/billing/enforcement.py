"""User limit enforcement for per-seat billing.

Checks whether the current user count is at or over the subscription
limit and returns a status object the caller can act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.adapters import get_billing_config, get_user_count
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


async def check_user_limit(db: AsyncSession) -> UserLimitStatus:
    """Check whether the instance is at or over its subscription user limit.

    Returns UserLimitStatus with enforcement="none" when:
    - No billing config exists (self-hosted, no Stripe)
    - No subscription is linked
    - Subscription quantity is 0 (treat as unlimited)

    Callers decide what to do with the result:
    - enforcement="hard" + at_limit=True → block user creation
    - enforcement="soft" + at_limit=True → log warning, allow creation
    - enforcement="none" → no limit, allow creation
    """
    billing_config = await get_billing_config(db)

    if not billing_config or not billing_config.get("stripe_subscription_id"):
        return _NO_LIMIT

    user_limit = billing_config.get("quantity", 0)
    if user_limit == 0:
        return _NO_LIMIT

    enforcement = billing_config.get("user_limit_enforcement", "soft")

    if enforcement == "hard":
        # Serialise concurrent user-creation requests at the DB level.
        # Acquiring billing_state FOR UPDATE means the second request blocks
        # here until the first has committed its INSERT, so both cannot pass
        # the limit check simultaneously with the same user count.
        from shu.billing.state_service import BillingStateService
        await BillingStateService.get_for_update(db)

    current_count = await get_user_count(db)
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
