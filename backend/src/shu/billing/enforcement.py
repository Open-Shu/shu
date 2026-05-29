"""User limit enforcement for per-seat billing.

Checks whether the current user count is at or over the subscription
limit and returns a status object the caller can act on.

Also hosts the SHU-703 subscription-active gate consumed by every
billable chokepoint (OCR, embedding, chat, KB upload). Keeping the
two helpers in one module mirrors the "billing enforcement" boundary
in the design doc — there is no per-chokepoint policy.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from shu.billing.adapters import (
    UsageProviderImpl,
    get_active_user_count,
    get_billing_config,
)
from shu.billing.billing_state_cache import get_billing_state_cache
from shu.billing.config import get_billing_settings
from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.entitlements import EntitlementDeniedError, LimitExceededError, LimitKey
from shu.billing.markup import resolve_markup
from shu.billing.state_service import BillingStateService
from shu.billing.stripe_client import StripeClient
from shu.core.database import get_async_session_local
from shu.core.exceptions import ShuException
from shu.core.logging import get_logger
from shu.models.document import Document
from shu.models.knowledge_base import KnowledgeBase

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


class HardCapExhaustedError(ShuException):
    """Raised when a hard-capped tenant has spent through their grant pool.

    Hard-capped covers both trial subscriptions (any tier) and the free
    tier whether trialing or not — CP unifies both via `hard_cap` on the
    wire (SHU-813). Distinct from `SubscriptionInactiveError` so the
    frontend can render the cap-exhausted surface (Upgrade now / Cancel
    trial) instead of the payment-failure surface (Update payment method).
    """

    def __init__(
        self,
        *,
        period_end: datetime | None,
        total_grant_amount: Decimal,
    ) -> None:
        # `period_end` (was `trial_deadline` pre-SHU-813) is the "budget
        # resets on …" anchor for both surfaces: during `trialing` it equals
        # the trial deadline, on the free tier it's the regular cycle end.
        # Either way the frontend has a non-null datetime to render.
        super().__init__(
            message="Usage budget exhausted.",
            error_code="hard_cap_exhausted",
            status_code=402,
            details={
                "period_end": period_end.isoformat() if period_end else None,
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
    cache = await get_billing_state_cache()
    if cache is None:
        return HEALTHY_DEFAULT
    return await cache.get()


async def assert_subscription_active() -> None:
    """Gate every billable chokepoint on payment-status AND hard-cap usage.

    Two independent failure modes share this single entry point so call
    sites (chat / embed / OCR / KB upload / worker handlers) get both
    checks without per-site wiring. Hard-capped subscriptions are still
    "active" in Stripe's sense; treating cap-exhaustion as another mode
    of "not active right now" keeps the assertion semantically honest.

    Precedence: payment failure raises first. A `past_due` tenant who
    happens to be hard-capped should see the payment-failure surface
    (it's the binding gate), not the cap-exhausted one.
    """
    cache = await get_billing_state_cache()

    # Self-hosted / dev: cache singleton missing → no enforcement at all.
    # Without this guard, `HEALTHY_DEFAULT.hard_cap=True` (the cold-start
    # fail-closed posture) would route self-hosted dev tenants into the
    # hard-cap branch.
    if cache is None:
        return

    state = await cache.get()

    if state.openrouter_key_disabled:
        raise SubscriptionInactiveError(
            payment_failed_at=state.payment_failed_at,
            grace_deadline=state.grace_deadline,
        )

    if state.subscription_status == "canceled":
        raise SubscriptionInactiveError(
            payment_failed_at=state.payment_failed_at,
            grace_deadline=state.grace_deadline,
        )

    if not state.hard_cap:
        return

    # Hard-cap path: precise per-period DB query rather than reading
    # `state.remaining_grant_amount` from the cache. The cache value is
    # the snapshot CP held at last poll — too loose for cap-spend
    # enforcement where minutes of overage are real cost the company eats.
    # The period anchor (`current_period_start`) comes from the wire too,
    # with the cache's period_end freshness guard ensuring the anchor is
    # current right after Stripe rolls (trial conversion / cycle rollover).
    if state.current_period_start is None:
        # Fail-closed on missing period info: silent bypass would
        # let unbounded spend through on a data anomaly.
        raise HardCapExhaustedError(
            period_end=state.current_period_end,
            total_grant_amount=state.total_grant_amount,
        )
    session_local = get_async_session_local()
    async with session_local() as db:
        summary = await UsageProviderImpl(db).get_usage_summary(
            state.current_period_start,
            datetime.now(UTC),
        )
    # `total_cost_usd` is raw provider cost; `total_grant_amount` is
    # customer-billed (from CP/Stripe credit grant). Apply the markup
    # before comparing so the cap triggers on the billed dollar the
    # customer would have been charged, not the raw provider dollar.
    billed_cost = summary.total_cost_usd * resolve_markup(state)
    if billed_cost >= state.total_grant_amount:
        raise HardCapExhaustedError(
            period_end=state.current_period_end,
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

    # `soft` is preserved as a distinct mode (SHU-784): registration is
    # allowed past the seat limit, but the warn-and-proceed path in
    # `api/auth.py` logs the over-limit event and the new user lands
    # inactive via the normal `SHU_AUTO_ACTIVATE_USERS=false` path. Admins
    # decide whether to bring the user on. `hard` still blocks at 403.
    enforcement = billing_config.get("user_limit_enforcement", "soft")

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
    cache = await get_billing_state_cache()
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


async def _assert_count_under_limit(
    db: AsyncSession,
    *,
    limit_key: LimitKey,
    cap_attr: str,
    count_target: InstrumentedAttribute,
) -> None:
    """Shared body for the SHU-776 KB / document count gates.

    Self-hosted / dev (cache singleton missing) bypasses enforcement —
    matches `assert_entitlement`'s posture so dev environments aren't
    gated on a CP that isn't configured.

    Acquires the per-tenant `billing_state` row FOR UPDATE before counting
    so two concurrent creates can't both pass the check at `current = cap - 1`.
    Mirrors the pattern in `check_user_limit`.

    `cap_attr` names the `LimitSet` field holding the cap; `count_target` is
    the model column to count (tenant-scoped via RLS).
    """
    counted = await _locked_count_and_cap(db, cap_attr=cap_attr, count_target=count_target)
    if counted is None:
        return
    current, cap = counted
    if current >= cap:
        raise LimitExceededError(limit=limit_key, cap=cap, current=current)


async def _locked_count_and_cap(
    db: AsyncSession,
    *,
    cap_attr: str,
    count_target: InstrumentedAttribute,
) -> tuple[int, int] | None:
    """Return `(current_count, cap)` under the per-tenant `billing_state` row lock,
    or `None` when enforcement is bypassed (self-hosted / no cache).

    The FOR UPDATE serialises against concurrent creates so two requests can't
    both pass at `current = cap - 1`; mirrors `check_user_limit`.
    """
    cache = await get_billing_state_cache()
    if cache is None:
        return None
    state = await cache.get()
    cap = getattr(state.limits, cap_attr)

    await BillingStateService.get_for_update(db)
    result = await db.execute(select(func.count(count_target)))
    current = result.scalar() or 0
    return current, cap


async def assert_kb_count_under_limit(db: AsyncSession) -> None:
    """Raise `LimitExceededError` if the tenant's KB count is at or over
    `BillingState.limits.kb_count_limit`. See `_assert_count_under_limit`.
    """
    await _assert_count_under_limit(
        db,
        limit_key="kb_count",
        cap_attr="kb_count_limit",
        count_target=KnowledgeBase.id,
    )


@dataclass
class _DocCountBatch:
    """Per-batch document-cap state.

    `remaining` is how many more documents the batch may create, seeded from one
    locked count on the first new document and decremented per document after —
    so the batch's own inserts can't overshoot the cap. `bypass` short-circuits
    self-hosted. `seeded` guards the one-time count.
    """

    seeded: bool = False
    bypass: bool = False
    cap: int = 0
    remaining: int = 0


# Active only inside `document_count_batch()`. When set, the document-cap gate
# counts once for the whole batch and then tracks remaining capacity in memory.
_doc_count_batch: ContextVar[_DocCountBatch | None] = ContextVar("doc_count_batch", default=None)


@contextlib.asynccontextmanager
async def document_count_batch():
    """Scope a bulk-ingestion run so the document cap is counted once, not per doc.

    Feed/plugin runs ingest many documents through `DocumentService.create_document`,
    each of which would otherwise take `billing_state` FOR UPDATE and run a full
    COUNT — needless contention on a sequential batch. Inside this scope the first
    new document runs the authoritative locked count to seed remaining capacity;
    each subsequent document decrements it and is rejected once it hits zero, so a
    batch cannot exceed the cap with its own inserts.

    Residual: the seed count is a snapshot, so two *concurrent* batches for the
    same tenant could each fill the headroom and overshoot by up to one batch's
    worth — bounded, and the next run blocks. Direct user uploads run outside this
    scope and stay strictly per-document.
    """
    token = _doc_count_batch.set(_DocCountBatch())
    try:
        yield
    finally:
        _doc_count_batch.reset(token)


async def assert_document_count_under_limit(db: AsyncSession) -> None:
    """Raise `LimitExceededError` if the tenant's document count is at or over
    `BillingState.limits.document_count_limit`. Cap is a per-tenant total
    across all KBs. See `_assert_count_under_limit`.

    Outside a `document_count_batch()` scope this is a per-call authoritative
    check (direct uploads). Inside one, the first call seeds remaining capacity
    from a single locked count and each call consumes one — so the batch counts
    the DB once but still can't push its own inserts past the cap.
    """
    batch = _doc_count_batch.get()
    if batch is None:
        await _assert_count_under_limit(
            db,
            limit_key="document_count",
            cap_attr="document_count_limit",
            count_target=Document.id,
        )
        return

    if not batch.seeded:
        batch.seeded = True
        counted = await _locked_count_and_cap(db, cap_attr="document_count_limit", count_target=Document.id)
        if counted is None:
            batch.bypass = True
        else:
            current, batch.cap = counted
            batch.remaining = batch.cap - current

    if batch.bypass:
        return
    if batch.remaining <= 0:
        # The batch has consumed its headroom; report the cap as the current
        # count since that's where the batch's own inserts have landed it.
        raise LimitExceededError(limit="document_count", cap=batch.cap, current=batch.cap)
    batch.remaining -= 1


async def assert_document_count_under_limit_for_upload() -> None:
    """Parameterless FastAPI dependency for the document-upload route.

    Fast-fails an over-cap upload before bytes are staged or OCR jobs are
    enqueued. Opens its own short-lived session — mirroring
    `assert_subscription_active` — so the FOR UPDATE acquired inside
    `assert_document_count_under_limit` releases right after the count
    rather than being held for the whole upload request. The authoritative,
    race-safe gate still runs in `DocumentService.create_document`.
    """
    session_local = get_async_session_local()
    async with session_local() as db:
        await assert_document_count_under_limit(db)
