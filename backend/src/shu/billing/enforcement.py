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
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.adapters import get_billing_config, get_user_count
from shu.billing.billing_state_cache import get_billing_state_cache
from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
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
    """Raise ``SubscriptionInactiveError`` if CP has paused service.

    The deadline is recomputed from the current cache payload on every
    call rather than memoized — `payment_grace_days` is CP-served and
    can change mid-window, so caching the derived deadline would lock
    in a stale window length.
    """
    state = await get_current_billing_state()
    if not state.openrouter_key_disabled:
        return

    grace_deadline: datetime | None = None
    if state.payment_failed_at is not None:
        grace_deadline = state.payment_failed_at + timedelta(days=state.payment_grace_days)

    raise SubscriptionInactiveError(
        payment_failed_at=state.payment_failed_at,
        grace_deadline=grace_deadline,
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
