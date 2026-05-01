"""Tests for SeatService — admin-intent-driven Stripe quantity writes."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.seat_service import (
    ProrationPreview,
    SeatMinimumError,
    SeatService,
    UserNotFoundError,
    UserStateError,
)
from shu.billing.stripe_client import StripeClientError


def _patch_seat_module(*, active_count=0, limit_status=None):
    """Context-managed patches for seat_service module-level helpers.

    Replaces the previous direct-rebind pattern (``seat_module.x = ...``) which
    leaked mocks across test invocations. Use as ``with _patch_seat_module(...):``.
    """
    status = limit_status or MagicMock(
        enforcement="hard", at_limit=False, current_count=active_count, user_limit=active_count + 1
    )
    return (
        patch("shu.billing.seat_service.get_active_user_count", AsyncMock(return_value=active_count)),
        patch("shu.billing.seat_service.check_user_limit", AsyncMock(return_value=status)),
    )


def _patch_flag_inputs(*, active_count: int, flagged_count: int):
    """Patch the helpers ``flag_user`` reads to decide drop-vs-label.

    flag_user computes ``expected_active_after = active - flagged_before - 1``;
    these two patches let a test put the system in any (active, flagged) state.
    """
    return (
        patch("shu.billing.seat_service.get_active_user_count", AsyncMock(return_value=active_count)),
        patch("shu.billing.seat_service._count_flagged_active", AsyncMock(return_value=flagged_count)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERIOD_END_EPOCH = int(datetime(2026, 5, 1, tzinfo=UTC).timestamp())
_SUB_ID = "sub_test_1"


def _make_subscription(quantity: int) -> dict:
    """Return a subscription dict shaped like the Stripe SDK response."""
    return {
        "id": _SUB_ID,
        "current_period_end": _PERIOD_END_EPOCH,
        "items": {
            "data": [
                {
                    "id": "si_seat",
                    "quantity": quantity,
                    "price": {"unit_amount": 1000, "recurring": {"usage_type": "licensed"}},
                }
            ]
        },
    }


def _make_state(
    subscription_id: str | None = _SUB_ID,
) -> MagicMock:
    state = MagicMock()
    state.stripe_subscription_id = subscription_id
    return state


def _make_services(
    *,
    quantity: int = 3,
    target_quantity: int | None = None,
    schedule_id: str | None = None,
    state: MagicMock | None = None,
) -> tuple[SeatService, AsyncMock, MagicMock]:
    """Build a SeatService with stub stripe_client / state_service.

    `target_quantity` defaults to `quantity`. Pass them differently to model
    Stripe's live quantity diverging from its scheduled next-cycle target.
    """
    stripe_client = AsyncMock()
    stripe_client.get_subscription = AsyncMock(return_value=_make_subscription(quantity))
    stripe_client.get_subscription_seat_state = AsyncMock(
        return_value=(quantity, target_quantity if target_quantity is not None else quantity, schedule_id)
    )
    stripe_client.update_subscription_quantity = AsyncMock(
        return_value=(_make_subscription(quantity), True)
    )

    state_service = MagicMock()
    resolved_state = state or _make_state()
    state_service.get = AsyncMock(return_value=resolved_state)
    state_service.get_for_update = AsyncMock(return_value=resolved_state)
    state_service.update = AsyncMock()

    return SeatService(stripe_client=stripe_client, state_service=state_service), stripe_client, state_service


def _make_user(user_id: int = 1, *, is_active: bool = True, flagged_at=None) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.is_active = is_active
    user.deactivation_scheduled_at = flagged_at
    return user


def _make_db(user: MagicMock | None = None, *, count: int = 0) -> AsyncMock:
    """AsyncSession mock with db.get returning user, and db.execute returning count."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar = MagicMock(return_value=count)
    db.execute = AsyncMock(return_value=mock_result)
    return db


# ---------------------------------------------------------------------------
# G1 — preview_upgrade + confirm_upgrade
# ---------------------------------------------------------------------------


class TestPreviewUpgrade:
    @pytest.mark.asyncio
    async def test_preview_upgrade_success_returns_proration_from_delta(self):
        """Baseline→proposed invoice delta drives amount_usd."""
        service, stripe_client, _ = _make_services(quantity=3)
        stripe_client.get_upcoming_invoice = AsyncMock(
            side_effect=[
                {"amount_due": 5000},  # baseline (3 seats)
                {"amount_due": 7500},  # proposed (4 seats)
            ]
        )
        preview = await service.preview_upgrade(AsyncMock())

        assert isinstance(preview, ProrationPreview)
        assert preview.amount_usd == "25.00"  # (7500 - 5000) / 100
        assert preview.cost_per_seat_usd == "10.00"  # 1000 cents / 100
        assert preview.period_end.year == 2026

    @pytest.mark.asyncio
    async def test_preview_upgrade_stripe_failure_returns_none(self):
        """Any exception during preview → log WARNING + return None."""
        service, stripe_client, _ = _make_services(quantity=3)
        stripe_client.get_upcoming_invoice = AsyncMock(
            side_effect=StripeClientError("boom")
        )
        preview = await service.preview_upgrade(AsyncMock())
        assert preview is None

    @pytest.mark.asyncio
    async def test_preview_upgrade_no_subscription_returns_none(self):
        service, stripe_client, state_service = _make_services(quantity=3)
        state_service.get = AsyncMock(return_value=_make_state(subscription_id=None))
        assert await service.preview_upgrade(AsyncMock()) is None


class TestConfirmUpgrade:
    @pytest.mark.asyncio
    async def test_confirm_upgrade_bumps_live_by_one(self):
        """live=3 → primitive called with target=4; no local seat-count write.

        confirm_upgrade reads live qty from Stripe (not local target) so the
        upgrade reflects the modal's "add 1 seat" promise.
        """
        service, stripe_client, state_service = _make_services(quantity=3, target_quantity=3)
        # After upgrade, Stripe returns the subscription at the new qty.
        stripe_client.update_subscription_quantity = AsyncMock(
            return_value=(_make_subscription(4), True)
        )
        await service.confirm_upgrade(AsyncMock())

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=4)
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_upgrade_preserves_pending_relative_reduction(self):
        """Pending downgrade pre-existed (live=4, target=2 → reduction of 2).

        Confirm bumps live by 1 (live=5) and recreates the schedule at the
        same relative offset (target=3, reduction still 2). Two Stripe calls:
        the first bumps live (releases prior schedule), the second installs
        a new schedule for the preserved reduction.
        """
        service, stripe_client, state_service = _make_services(quantity=4, target_quantity=2)
        # Both Stripe calls return a subscription with new live=5.
        stripe_client.update_subscription_quantity = AsyncMock(
            side_effect=[
                (_make_subscription(5), True),  # call 1: bump live to 5
                (_make_subscription(5), True),  # call 2: schedule recreated at target=3
            ]
        )
        await service.confirm_upgrade(AsyncMock())

        assert stripe_client.update_subscription_quantity.await_count == 2
        calls = stripe_client.update_subscription_quantity.call_args_list
        assert calls[0].kwargs == {"target": 5}
        assert calls[1].kwargs == {"target": 3}
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_upgrade_raises_on_stripe_failure(self):
        service, stripe_client, state_service = _make_services(target_quantity=3)
        stripe_client.update_subscription_quantity = AsyncMock(
            side_effect=StripeClientError("stripe down")
        )
        with pytest.raises(StripeClientError):
            await service.confirm_upgrade(AsyncMock())

        # Stripe-first ordering: no DB write when Stripe failed.
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_upgrade_raises_when_no_subscription(self):
        service, _, state_service = _make_services()
        # confirm_upgrade now reads state through the locked path; override
        # both helpers so the "subscription_id missing" branch is reachable.
        empty_state = _make_state(subscription_id=None)
        state_service.get = AsyncMock(return_value=empty_state)
        state_service.get_for_update = AsyncMock(return_value=empty_state)
        with pytest.raises(StripeClientError):
            await service.confirm_upgrade(AsyncMock())


# ---------------------------------------------------------------------------
# G2 — flag_user / unflag_user
# ---------------------------------------------------------------------------


class TestFlagUser:
    @pytest.mark.asyncio
    async def test_flag_drops_target_when_no_pending_slot_exists(self):
        """target=3, active=3, flagged=0: full house, flag genuinely shrinks paid capacity → target=2."""
        service, stripe_client, state_service = _make_services(target_quantity=3)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch:
            await service.flag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=2)
        assert user.deactivation_scheduled_at is not None
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_compounds_on_open_seat_release(self):
        """live=4, target=3 (open seat released), active=3, flagged=0 → flag drops to 2.

        Released open seat created slack (target=3 > expected_after=2), so the
        flag genuinely shrinks paid capacity. Ends at target=2 next cycle.
        """
        service, stripe_client, state_service = _make_services(quantity=4, target_quantity=3)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch:
            await service.flag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=2)
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_claims_pending_slot_without_decrementing(self):
        """live=3, target=2, active=3, flagged=0 (downgrade pending, no user marked).

        Existing schedule already commits to dropping 1 seat at rollover —
        flagging a user just labels which one fills that slot. No Stripe write.
        """
        service, stripe_client, state_service = _make_services(quantity=3, target_quantity=2)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch:
            await service.flag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_not_awaited()
        assert user.deactivation_scheduled_at is not None
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_retry_after_commit_failure_is_idempotent(self):
        """Stripe write succeeded but db.commit failed — retry must not double-decrement.

        State after the failed first attempt: target dropped (e.g. 3→2) but
        the user wasn't persisted as flagged. On retry, current_target=2,
        active=3, flagged_before=0 → expected_active_after=2; current_target
        is not strictly greater, so the retry skips the redundant drop.
        Without this, the retry would land at target=1 and force-deactivate
        an unrelated user at rollover.
        """
        service, stripe_client, state_service = _make_services(quantity=3, target_quantity=2)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch:
            await service.flag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_not_awaited()
        assert user.deactivation_scheduled_at is not None
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_user_stripe_first_aborts_on_stripe_error(self):
        """Stripe failure must leave DB untouched — Stripe-first ordering."""
        service, stripe_client, state_service = _make_services(target_quantity=3)
        stripe_client.update_subscription_quantity = AsyncMock(
            side_effect=StripeClientError("nope")
        )
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch, pytest.raises(StripeClientError):
            await service.flag_user(db, user_id=7)

        assert user.deactivation_scheduled_at is None
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flag_user_rejects_if_already_flagged(self):
        service, _, _ = _make_services(target_quantity=3)
        user = _make_user(user_id=7, is_active=True, flagged_at=datetime.now(UTC))
        db = _make_db(user)

        with pytest.raises(UserStateError):
            await service.flag_user(db, user_id=7)

    @pytest.mark.asyncio
    async def test_flag_user_rejects_if_inactive(self):
        service, _, _ = _make_services(target_quantity=3)
        user = _make_user(user_id=7, is_active=False)
        db = _make_db(user)

        with pytest.raises(UserStateError):
            await service.flag_user(db, user_id=7)

    @pytest.mark.asyncio
    async def test_flag_user_404_when_user_missing(self):
        service, _, _ = _make_services(target_quantity=3)
        db = _make_db(user=None)

        with pytest.raises(UserNotFoundError):
            await service.flag_user(db, user_id=7)

    @pytest.mark.asyncio
    async def test_flag_user_seat_minimum_blocks_below_one(self):
        """target=1, active=1, flagged=0 → flagging would drop paid below 1 → SeatMinimumError."""
        service, stripe_client, _ = _make_services(target_quantity=1)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=1, flagged_count=0)
        with active_patch, flagged_patch, pytest.raises(SeatMinimumError):
            await service.flag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_not_awaited()


class TestUnflagUser:
    @pytest.mark.asyncio
    async def test_unflag_user_increments_target_by_one(self):
        """Pending Stripe target=2 vs live=3 → unflag stacks target back to 3."""
        service, stripe_client, state_service = _make_services(quantity=3, target_quantity=2)
        user = _make_user(user_id=7, is_active=True, flagged_at=datetime.now(UTC))
        db = _make_db(user)

        await service.unflag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=3)
        assert user.deactivation_scheduled_at is None
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unflag_does_not_bump_live_when_target_already_at_live(self):
        """target == live (no pending reduction) → unflag clears flag only.

        Earlier behaviour did ``target += 1`` blindly, which routed through
        the upgrade path and silently bumped live qty. Capping at live qty
        prevents that — unflag is billing-neutral when no downgrade was
        actually pending.
        """
        service, stripe_client, state_service = _make_services(quantity=3, target_quantity=3)
        user = _make_user(user_id=7, is_active=True, flagged_at=datetime.now(UTC))
        db = _make_db(user)

        await service.unflag_user(db, user_id=7)

        stripe_client.update_subscription_quantity.assert_not_awaited()
        state_service.update.assert_not_awaited()
        assert user.deactivation_scheduled_at is None
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_unflag_rejects_if_not_currently_flagged(self):
        service, _, _ = _make_services(target_quantity=3)
        user = _make_user(user_id=7, is_active=True, flagged_at=None)
        db = _make_db(user)

        with pytest.raises(UserStateError):
            await service.unflag_user(db, user_id=7)


# ---------------------------------------------------------------------------
# G3 — release_open_seat
# ---------------------------------------------------------------------------


class TestReleaseOpenSeat:
    @pytest.mark.asyncio
    async def test_release_open_seat_decrements_target_by_one(self):
        """Stripe target=4, active=2 → primitive at target=3."""
        service, stripe_client, state_service = _make_services(target_quantity=4)
        db = _make_db()

        active_patch, limit_patch = _patch_seat_module(active_count=2)
        with active_patch, limit_patch:
            await service.release_open_seat(db)

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=3)
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_open_seat_compounds_on_pending_release(self):
        """target_quantity=3 (after a previous release from 4), active=2 → release lands at 2."""
        service, stripe_client, state_service = _make_services(quantity=4, target_quantity=3)
        db = _make_db()

        active_patch, limit_patch = _patch_seat_module(active_count=2)
        with active_patch, limit_patch:
            await service.release_open_seat(db)

        stripe_client.update_subscription_quantity.assert_awaited_once_with(_SUB_ID, target=2)
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_open_seat_refuses_below_one(self):
        """target_quantity=1 → release would drop to 0 → SeatMinimumError."""
        service, stripe_client, state_service = _make_services(target_quantity=1)
        db = _make_db()

        with pytest.raises(SeatMinimumError):
            await service.release_open_seat(db)
        stripe_client.update_subscription_quantity.assert_not_awaited()
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_open_seat_refuses_when_no_headroom(self):
        """target=4, active=4 → no open seat. Release must reject; admin should flag a user instead."""
        service, stripe_client, state_service = _make_services(target_quantity=4)
        db = _make_db()

        active_patch, limit_patch = _patch_seat_module(active_count=4)
        with active_patch, limit_patch, pytest.raises(SeatMinimumError, match="No open seats to release"):
            await service.release_open_seat(db)
        stripe_client.update_subscription_quantity.assert_not_awaited()
        state_service.update.assert_not_awaited()


class TestCancelPendingRelease:
    @pytest.mark.asyncio
    async def test_releases_schedule_clears_flags_and_resets_target(self):
        """Wipes Stripe schedule and bulk-clears flags; no local target write."""
        service, stripe_client, state_service = _make_services(
            quantity=5,
            target_quantity=3,
            schedule_id="sub_sched_pending",
        )
        stripe_client.release_subscription_schedule = AsyncMock()

        active_patch, limit_patch = _patch_seat_module(
            limit_status=MagicMock(
                enforcement="hard", at_limit=False, current_count=4, user_limit=5
            )
        )
        db = _make_db()

        with active_patch, limit_patch:
            await service.cancel_pending_release(db)

        stripe_client.release_subscription_schedule.assert_awaited_once_with("sub_sched_pending")
        # Bulk UPDATE on User to clear flags is executed.
        db.execute.assert_awaited()
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_release_when_no_schedule_attached(self):
        """If no schedule is attached, still bulk-clear flags defensively."""
        service, stripe_client, state_service = _make_services(quantity=5, target_quantity=5)
        stripe_client.release_subscription_schedule = AsyncMock()

        active_patch, limit_patch = _patch_seat_module(
            limit_status=MagicMock(
                enforcement="hard", at_limit=False, current_count=4, user_limit=5
            )
        )
        db = _make_db()

        with active_patch, limit_patch:
            await service.cancel_pending_release(db)

        stripe_client.release_subscription_schedule.assert_not_awaited()
        state_service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stripe_first_aborts_on_release_failure(self):
        """Stripe failure during release_subscription_schedule must leave DB untouched."""
        service, stripe_client, state_service = _make_services(
            quantity=5,
            target_quantity=3,
            schedule_id="sub_sched_pending",
        )
        stripe_client.release_subscription_schedule = AsyncMock(
            side_effect=StripeClientError("stripe down")
        )

        db = _make_db()

        with pytest.raises(StripeClientError):
            await service.cancel_pending_release(db)

        state_service.update.assert_not_awaited()
        # The bulk-clear UPDATE on users must NOT run if Stripe failed.
        db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# G4 — rollover
# ---------------------------------------------------------------------------


class TestRollover:
    """Uses AsyncMock for the session — rollover's SQL paths are verified by
    asserting the exact mutations on the returned user rows and that the
    bulk UPDATE statement is executed.
    """

    @pytest.mark.asyncio
    async def test_rollover_deactivates_flagged_users(self):
        service, _, _ = _make_services(quantity=2)

        flagged_one = _make_user(user_id=1, is_active=True, flagged_at=datetime.now(UTC))
        flagged_two = _make_user(user_id=2, is_active=True, flagged_at=datetime.now(UTC))

        db = AsyncMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()

        flagged_result = MagicMock()
        flagged_result.scalars.return_value.all.return_value = [flagged_one, flagged_two]
        # active count after deactivating flagged
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        # bulk UPDATE — no rows returned
        update_result = MagicMock()
        db.execute = AsyncMock(side_effect=[flagged_result, count_result, update_result])

        await service.rollover(db, _SUB_ID, "evt_1")

        assert flagged_one.is_active is False
        assert flagged_two.is_active is False
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollover_picks_extras_by_created_at_desc_when_undershoot(self):
        """active_count > quantity after flagged sweep → deactivate extras."""
        service, _, _ = _make_services(quantity=1)

        extra_user = _make_user(user_id=99, is_active=True)

        db = AsyncMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()

        flagged_result = MagicMock()
        flagged_result.scalars.return_value.all.return_value = []
        count_result = MagicMock()
        count_result.scalar.return_value = 2  # over quantity by 1
        extras_result = MagicMock()
        extras_result.scalars.return_value.all.return_value = [extra_user]
        update_result = MagicMock()
        db.execute = AsyncMock(
            side_effect=[flagged_result, count_result, extras_result, update_result]
        )

        await service.rollover(db, _SUB_ID, "evt_2")

        assert extra_user.is_active is False

    @pytest.mark.asyncio
    async def test_rollover_redelivery_is_noop(self):
        """Second delivery: zero flagged + active_count <= quantity → no mutations."""
        service, _, _ = _make_services(quantity=3)

        db = AsyncMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()

        flagged_result = MagicMock()
        flagged_result.scalars.return_value.all.return_value = []
        count_result = MagicMock()
        count_result.scalar.return_value = 2  # under quantity
        update_result = MagicMock()
        db.execute = AsyncMock(side_effect=[flagged_result, count_result, update_result])

        await service.rollover(db, _SUB_ID, "evt_redelivery")

        # Only three execute() calls — no "extras" SELECT, no user updates.
        assert db.execute.await_count == 3
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollover_raises_when_subscription_missing(self):
        service, stripe_client, _ = _make_services()
        stripe_client.get_subscription = AsyncMock(return_value=None)

        with pytest.raises(StripeClientError):
            await service.rollover(AsyncMock(), _SUB_ID, "evt_missing")


class TestSeatMutationLocking:
    """Every seat-mutation path must acquire ``billing_state.get_for_update``.

    Two admins flagging different users (or one flagging while another
    releases) would otherwise both compute from the same pre-write Stripe
    state and leave Stripe target inconsistent with the final flag set.
    """

    @pytest.mark.asyncio
    async def test_confirm_upgrade_locks_billing_state(self):
        service, _, state_service = _make_services(target_quantity=3)
        await service.confirm_upgrade(AsyncMock())
        state_service.get_for_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_flag_user_locks_billing_state(self):
        service, _, state_service = _make_services(target_quantity=3)
        user = _make_user(user_id=7, is_active=True)
        db = _make_db(user)

        active_patch, flagged_patch = _patch_flag_inputs(active_count=3, flagged_count=0)
        with active_patch, flagged_patch:
            await service.flag_user(db, user_id=7)

        state_service.get_for_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_unflag_user_locks_billing_state(self):
        service, _, state_service = _make_services(quantity=3, target_quantity=2)
        user = _make_user(user_id=7, is_active=True, flagged_at=datetime.now(UTC))
        db = _make_db(user)

        await service.unflag_user(db, user_id=7)
        state_service.get_for_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_release_open_seat_locks_billing_state(self):
        service, _, state_service = _make_services(target_quantity=4)
        db = _make_db()
        active_patch, limit_patch = _patch_seat_module(active_count=2)
        with active_patch, limit_patch:
            await service.release_open_seat(db)
        state_service.get_for_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_cancel_pending_release_locks_billing_state(self):
        service, stripe_client, state_service = _make_services(quantity=5, target_quantity=5)
        stripe_client.release_subscription_schedule = AsyncMock()

        active_patch, limit_patch = _patch_seat_module(
            limit_status=MagicMock(
                enforcement="hard", at_limit=False, current_count=4, user_limit=5
            )
        )
        db = _make_db()
        with active_patch, limit_patch:
            await service.cancel_pending_release(db)
        state_service.get_for_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_rollover_locks_billing_state(self):
        service, _, state_service = _make_services(quantity=2)
        db = AsyncMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()
        flagged_result = MagicMock()
        flagged_result.scalars.return_value.all.return_value = []
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        update_result = MagicMock()
        db.execute = AsyncMock(side_effect=[flagged_result, count_result, update_result])

        await service.rollover(db, _SUB_ID, "evt_lock")
        state_service.get_for_update.assert_awaited()
