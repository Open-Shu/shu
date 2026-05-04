"""Seat management service — admin-intent-driven Stripe quantity writes.

Wraps the SHU-704 `update_subscription_quantity` primitive with the
higher-level operations surfaced by SHU-730: preview/confirm upgrade on
create/activate, flag/unflag a user for period-end deactivation, release
an open seat, and rollover reconciliation at invoice.paid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import Subscription

from shu.auth.models import User
from shu.billing.adapters import get_active_user_count
from shu.billing.config import BillingSettings, get_billing_settings_dependency
from shu.billing.enforcement import UserLimitStatus, check_user_limit
from shu.billing.service import BillingService
from shu.billing.state_service import BillingStateService
from shu.billing.stripe_client import (
    StripeClient,
    StripeClientError,
    find_seat_item,
    resolve_period_end,
)
from shu.core.logging import get_logger

logger = get_logger(__name__)


class SeatServiceError(Exception):
    """Base for seat-service domain errors."""


class UserNotFoundError(SeatServiceError):
    """The target user_id does not exist."""


class UserStateError(SeatServiceError):
    """The user is not in a state where this operation is valid."""


class SeatMinimumError(SeatServiceError):
    """The requested change would drop Stripe seat quantity below 1."""


@dataclass(frozen=True)
class ProrationPreview:
    """Proration preview for the 402 phase-1 seat-charge response.

    Both monetary fields are decimal-formatted strings so cents survive
    the wire (an integer dollars field would silently truncate $19.99 to
    "19" and break the frontend's recurring-rate math).
    """

    amount_usd: str
    period_end: datetime
    cost_per_seat_usd: str


class SeatService:
    """Admin-intent-driven seat quantity writes on Stripe.

    All mutations go through `StripeClient.update_subscription_quantity`
    (SHU-704 primitive) so upgrade/downgrade/schedule-update/release-schedule
    branching lives in one place. `BillingStateService` is used only for
    subscription-id lookup; Stripe owns live and next-cycle seat counts.
    """

    def __init__(
        self,
        stripe_client: StripeClient,
        state_service: type[BillingStateService],
    ) -> None:
        self.stripe_client = stripe_client
        self.state_service = state_service

    async def preview_upgrade(self, db: AsyncSession) -> ProrationPreview | None:
        """Build the 402 phase-1 proration preview for adding one seat.

        Best-effort: any Stripe error or missing field returns None so the
        caller can omit the price block from the response instead of 500-ing.
        """
        try:
            state = await self.state_service.get(db)
            if state is None or not state.stripe_subscription_id:
                return None
            subscription_id = state.stripe_subscription_id

            subscription = await self.stripe_client.get_subscription(subscription_id)
            if subscription is None:
                return None
            seat_item = find_seat_item(subscription)
            if seat_item is None:
                return None

            current_qty = int(seat_item["quantity"])
            seat_item_id = seat_item["id"]
            unit_amount_cents = int(seat_item["price"]["unit_amount"])
            period_end = datetime.fromtimestamp(resolve_period_end(subscription, seat_item), tz=UTC)

            baseline = await self.stripe_client.get_upcoming_invoice(subscription_id)
            proposed = await self.stripe_client.get_upcoming_invoice(
                subscription_id,
                subscription_items=[{"id": seat_item_id, "quantity": current_qty + 1}],
                subscription_proration_behavior="create_prorations",
            )
            if baseline is None or proposed is None:
                return None

            delta_cents = int(proposed["amount_due"]) - int(baseline["amount_due"])
            amount_usd = f"{delta_cents / 100:.2f}"
            cost_per_seat_usd = f"{unit_amount_cents / 100:.2f}"

            return ProrationPreview(
                amount_usd=amount_usd,
                period_end=period_end,
                cost_per_seat_usd=cost_per_seat_usd,
            )
        except Exception:
            logger.warning("preview_upgrade failed", exc_info=True)
            return None

    async def confirm_upgrade(self, db: AsyncSession) -> Subscription:
        """Apply a +1 seat upgrade to the Stripe subscription.

        Reads live + target from Stripe, bumps live by 1, and preserves any
        pending relative reduction by recreating the schedule at the new
        offset. Two Stripe calls when a reduction was pending; one otherwise.
        No DB writes for seat counts — Stripe is the source of truth.
        """
        sub_id = await self._lock_state_for_mutation(db)
        live, target, _ = await self.stripe_client.get_subscription_seat_state(sub_id)
        pending_reduction = max(0, live - target)
        new_live = live + 1
        new_target = new_live - pending_reduction

        updated, _ = await self.stripe_client.update_subscription_quantity(sub_id, target=new_live)
        if new_target < new_live:
            await self.stripe_client.update_subscription_quantity(sub_id, target=new_target)
        return updated

    async def flag_user(self, db: AsyncSession, user_id: str) -> None:
        """Schedule a user's deactivation at the next period end.

        Decrements the Stripe target by 1 only when no pending downgrade
        slot already covers this user — otherwise the flag just labels
        which user fills an existing scheduled reduction.

        Concretely: drop target by 1 when ``current_target > active -
        flagged_before - 1``. Skip the drop when the inequality flips,
        because the existing schedule was already going to force a
        deactivation at rollover and the flag just claims that slot. This
        avoids compounding ``3 → 2 → 1`` when the admin only intended a
        single one-seat reduction (also gives us natural idempotency on
        retry: a Stripe-write-then-commit-fail leaves target where the
        retry will read it, and the retry skips the redundant decrement).

        Stripe-first ordering: a Stripe failure leaves the DB untouched.
        """
        user = await db.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"User {user_id} not found")
        if user.deactivation_scheduled_at is not None:
            raise UserStateError(f"User {user_id} is already flagged for deactivation")
        if not user.is_active:
            raise UserStateError(f"User {user_id} is not active; cannot flag")

        sub_id = await self._lock_state_for_mutation(db)
        _, current_target, _ = await self.stripe_client.get_subscription_seat_state(sub_id)

        active_count = await get_active_user_count(db)
        flagged_before = await _count_flagged_active(db)
        # Active users that will remain after this flag fires at rollover.
        expected_active_after = max(0, active_count - flagged_before - 1)

        if current_target > expected_active_after:
            new_target = current_target - 1
            if new_target < 1:
                raise SeatMinimumError(
                    f"Flagging user {user_id} would drop seats below 1 (current target={current_target})"
                )
            await self.stripe_client.update_subscription_quantity(sub_id, target=new_target)

        user.deactivation_scheduled_at = datetime.now(UTC)
        await db.commit()

    async def unflag_user(self, db: AsyncSession, user_id: str) -> None:
        """Cancel a previously scheduled deactivation.

        Reads target + live from Stripe; new target = ``min(live, target+1)``.
        The cap ensures unflagging is billing-neutral when no downgrade was
        actually pending — earlier behaviour silently routed through the
        upgrade path and bumped live qty.
        """
        user = await db.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"User {user_id} not found")
        if user.deactivation_scheduled_at is None:
            raise UserStateError(f"User {user_id} is not currently flagged")

        sub_id = await self._lock_state_for_mutation(db)
        live, current_target, _ = await self.stripe_client.get_subscription_seat_state(sub_id)
        new_target = min(live, current_target + 1)

        if new_target != current_target:
            await self.stripe_client.update_subscription_quantity(sub_id, target=new_target)

        user.deactivation_scheduled_at = None
        await db.commit()

    async def release_open_seat(self, db: AsyncSession) -> UserLimitStatus:
        """Shrink the next-cycle target by one without touching user rows.

        Only valid when there's genuine headroom (target > active count).
        Releasing below active count would force a random trim at rollover —
        admins should flag specific users instead. Frontend disables the
        button when no headroom; this check is the defensive backstop.
        """
        sub_id = await self._lock_state_for_mutation(db)
        _, current_target, _ = await self.stripe_client.get_subscription_seat_state(sub_id)
        new_target = current_target - 1
        if new_target < 1:
            raise SeatMinimumError(f"Releasing would drop seats below 1 (current target={current_target})")

        active_count = await get_active_user_count(db)
        if new_target < active_count:
            raise SeatMinimumError(
                f"No open seats to release (target={current_target}, active={active_count}). "
                "Flag a user for deactivation instead."
            )

        await self.stripe_client.update_subscription_quantity(sub_id, target=new_target)
        return await check_user_limit(db, self.stripe_client)

    async def cancel_pending_release(self, db: AsyncSession) -> UserLimitStatus:
        """Wipe all pending downgrade work — release schedule + clear all flags."""
        sub_id = await self._lock_state_for_mutation(db)
        _, _, schedule_id = await self.stripe_client.get_subscription_seat_state(sub_id)
        if schedule_id:
            await self.stripe_client.release_subscription_schedule(schedule_id)

        clear_stmt = (
            update(User)
            .where(User.deactivation_scheduled_at.is_not(None))
            .values(deactivation_scheduled_at=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(clear_stmt)
        await db.commit()
        return await check_user_limit(db, self.stripe_client)

    async def rollover(
        self,
        db: AsyncSession,
        subscription_id: str,
        stripe_event_id: str,
    ) -> None:
        """Reconcile local users to the quantity Stripe actually billed at cycle rollover.

        Invoked by the `invoice.paid` webhook when `billing_reason ==
        "subscription_cycle"`. Three passes:

        1. Deactivate every flagged (scheduled) user.
        2. If active users still outnumber Stripe quantity, deactivate the
           shortfall by `created_at DESC` (newest first).
        3. Clear `deactivation_scheduled_at` on every row that has it set,
           whether or not this delivery touched that user — keeps the flag
           from lingering past the cycle it targeted.

        Idempotency is natural: on a Stripe webhook redelivery, (1) finds
        no flagged rows, (2) finds active_count <= quantity, and (3) matches
        zero rows — all three branches no-op without any dedup table.
        """
        # Hold the billing_state lock for the same reason the admin paths do —
        # serialize rollover against any concurrent flag/unflag/release the
        # admin may fire while the webhook is in flight.
        await self.state_service.get_for_update(db)
        subscription = await self.stripe_client.get_subscription(subscription_id)
        if subscription is None:
            raise StripeClientError(f"Subscription {subscription_id!r} not found")
        seat_item = find_seat_item(subscription)
        if seat_item is None:
            raise StripeClientError(f"Subscription {subscription_id!r} has no licensed seat item")
        quantity = int(seat_item["quantity"])

        flagged_stmt = select(User).where(
            User.deactivation_scheduled_at.is_not(None),
            User.is_active.is_(True),
        )
        flagged = (await db.execute(flagged_stmt)).scalars().all()
        for user in flagged:
            user.is_active = False
            logger.info(
                "rollover.deactivated_flagged",
                extra={
                    "user_id": user.id,
                    "stripe_event_id": stripe_event_id,
                    "reason": "scheduled_deactivation",
                },
            )

        # Flush so the count below reflects the deactivations we just did.
        await db.flush()

        active_count_stmt = select(func.count(User.id)).where(User.is_active.is_(True))
        active_count = (await db.execute(active_count_stmt)).scalar() or 0
        shortfall = active_count - quantity
        if shortfall > 0:
            extras_stmt = select(User).where(User.is_active.is_(True)).order_by(User.created_at.desc()).limit(shortfall)
            extras = (await db.execute(extras_stmt)).scalars().all()
            for user in extras:
                user.is_active = False
                logger.info(
                    "rollover.deactivated_over_quantity",
                    extra={
                        "user_id": user.id,
                        "stripe_event_id": stripe_event_id,
                        "reason": "over_quantity",
                    },
                )

        # Spec-mandated: clear every flag in one statement so stray rows
        # (e.g. a flagged-but-already-inactive user from a race) don't
        # carry the deactivation marker into the next cycle.
        clear_stmt = (
            update(User)
            .where(User.deactivation_scheduled_at.is_not(None))
            .values(deactivation_scheduled_at=None)
            .execution_options(synchronize_session=False)
        )
        await db.execute(clear_stmt)

        await db.commit()

    async def _subscription_id(self, db: AsyncSession) -> str:
        """Return the configured Stripe subscription id, or raise.

        Read-only — does not acquire a lock. Use ``_lock_state_for_mutation``
        for paths that read-Stripe-then-write, so concurrent admin actions
        don't both compute from stale state.
        """
        state = await self.state_service.get(db)
        if state is None or not state.stripe_subscription_id:
            raise StripeClientError("No subscription configured")
        return state.stripe_subscription_id

    async def _lock_state_for_mutation(self, db: AsyncSession) -> str:
        """Serialize seat-mutation paths via ``SELECT ... FOR UPDATE`` on billing_state.

        Returns the subscription id. The lock is held until the caller's
        transaction commits or rolls back. Mirrors ``check_user_limit``'s
        existing serialization on the user-create path so that flag /
        unflag / release / cancel / confirm / rollover can't race each
        other and leave Stripe target inconsistent with local flags.
        """
        state = await self.state_service.get_for_update(db)
        if state is None or not state.stripe_subscription_id:
            raise StripeClientError("No subscription configured")
        return state.stripe_subscription_id


async def _count_flagged_active(db: AsyncSession) -> int:
    """Count active users currently flagged for period-end deactivation.

    Used by ``flag_user`` to decide whether the new flag adds reduction or
    claims an existing pending slot. Inactive flagged users are ignored —
    they don't consume a seat at rollover.
    """
    stmt = select(func.count(User.id)).where(
        User.deactivation_scheduled_at.is_not(None),
        User.is_active.is_(True),
    )
    return (await db.execute(stmt)).scalar() or 0


def get_seat_service(
    settings: Annotated[BillingSettings, Depends(get_billing_settings_dependency)],
) -> SeatService | None:
    """FastAPI dependency that yields a SeatService, or None when unconfigured.

    Returning None (rather than raising 503) keeps seat-aware admin routes
    like ``create_user`` / ``activate_user`` working on self-hosted deploys
    where Stripe isn't wired up — the preflight short-circuits on None.
    Routes that genuinely require Stripe (``/seats/release``) check
    explicitly and raise.

    Lives in this module (not router.py) to avoid the
    ``api.auth → billing.router → api.dependencies → api.__init__ → api.auth``
    circular-import chain.
    """
    if not settings.is_configured:
        return None
    service = BillingService(settings)
    return SeatService(stripe_client=service._client, state_service=BillingStateService)
