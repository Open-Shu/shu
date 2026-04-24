"""Tests for BillingStateService — locking, audit trail, and edge cases."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from shu.billing.state_service import BillingStateService, _to_json
from shu.models.billing_state import BillingState, BillingStateAudit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**kwargs) -> BillingState:
    """Build a BillingState instance with test defaults."""
    state = BillingState()
    state.id = 1
    state.stripe_customer_id = kwargs.get("stripe_customer_id", None)
    state.stripe_subscription_id = kwargs.get("stripe_subscription_id", None)
    state.billing_email = kwargs.get("billing_email", None)
    state.subscription_status = kwargs.get("subscription_status", "pending")
    state.current_period_start = kwargs.get("current_period_start", None)
    state.current_period_end = kwargs.get("current_period_end", None)
    state.quantity = kwargs.get("quantity", 0)
    state.cancel_at_period_end = kwargs.get("cancel_at_period_end", False)
    state.last_reported_total = kwargs.get("last_reported_total", 0)
    state.last_reported_period_start = kwargs.get("last_reported_period_start", None)
    state.payment_failed_at = kwargs.get("payment_failed_at", None)
    state.user_limit_enforcement = kwargs.get("user_limit_enforcement", "soft")
    state.version = kwargs.get("version", 0)
    state.updated_at = kwargs.get("updated_at", datetime(2026, 4, 1, tzinfo=UTC))
    return state


def _make_db(state: BillingState | None) -> AsyncMock:
    """Build a mock AsyncSession that returns ``state`` from execute()."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = state
    db.execute = AsyncMock(return_value=mock_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    # begin_nested() must return an async context manager (SAVEPOINT support)
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_cm)
    return db


# ---------------------------------------------------------------------------
# _to_json helper
# ---------------------------------------------------------------------------


class TestToJson:
    def test_converts_datetime_to_iso_string(self):
        dt = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        assert _to_json(dt) == "2026-04-01T12:00:00+00:00"

    def test_passes_through_int(self):
        assert _to_json(42) == 42

    def test_passes_through_none(self):
        assert _to_json(None) is None

    def test_passes_through_string(self):
        assert _to_json("active") == "active"

    def test_passes_through_bool(self):
        assert _to_json(True) is True


# ---------------------------------------------------------------------------
# BillingStateService.get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_state_when_exists(self):
        state = _make_state(stripe_customer_id="cus_123")
        db = _make_db(state)

        result = await BillingStateService.get(db)

        assert result is state

    @pytest.mark.asyncio
    async def test_returns_none_when_singleton_missing(self):
        db = _make_db(None)

        result = await BillingStateService.get(db)

        assert result is None


# ---------------------------------------------------------------------------
# BillingStateService.ensure_singleton
# ---------------------------------------------------------------------------


class TestEnsureSingleton:
    @pytest.mark.asyncio
    async def test_returns_existing_row_without_insert(self):
        state = _make_state()
        db = _make_db(state)

        result, inserted = await BillingStateService.ensure_singleton(db)

        assert result is state
        assert inserted is False
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_singleton_when_missing(self):
        db = _make_db(None)

        result, inserted = await BillingStateService.ensure_singleton(db)

        assert isinstance(result, BillingState)
        assert result.id == 1
        assert inserted is True
        db.add.assert_called_once_with(result)
        db.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# BillingStateService.update
# ---------------------------------------------------------------------------


class TestUpdate:
    @pytest.mark.asyncio
    async def test_applies_updates_to_state_row(self):
        state = _make_state(subscription_status="pending", quantity=0)
        db = _make_db(state)

        result = await BillingStateService.update(
            db,
            updates={"subscription_status": "active", "quantity": 5},
            source="webhook:subscription.updated",
        )

        assert result is state
        assert state.subscription_status == "active"
        assert state.quantity == 5

    @pytest.mark.asyncio
    async def test_increments_version_on_every_update(self):
        state = _make_state(version=3)
        db = _make_db(state)

        await BillingStateService.update(
            db,
            updates={"quantity": 10},
            source="scheduler:quantity_sync",
        )

        assert state.version == 4
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creates_audit_row_for_changed_field(self):
        state = _make_state(quantity=3)
        db = _make_db(state)

        await BillingStateService.update(
            db,
            updates={"quantity": 7},
            source="webhook:subscription.updated",
            stripe_event_id="evt_abc123",
        )

        # One audit row should have been added for the changed field
        assert db.add.call_count == 1
        audit_row = db.add.call_args[0][0]
        assert isinstance(audit_row, BillingStateAudit)
        assert audit_row.field_name == "quantity"
        assert audit_row.old_value == 3
        assert audit_row.new_value == 7
        assert audit_row.changed_by == "webhook:subscription.updated"
        assert audit_row.stripe_event_id == "evt_abc123"

    @pytest.mark.asyncio
    async def test_no_audit_row_when_value_unchanged(self):
        state = _make_state(subscription_status="active")
        db = _make_db(state)

        await BillingStateService.update(
            db,
            updates={"subscription_status": "active"},  # same value
            source="webhook:subscription.updated",
        )

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_one_audit_row_per_changed_field(self):
        state = _make_state(quantity=2, subscription_status="pending")
        db = _make_db(state)

        await BillingStateService.update(
            db,
            updates={"quantity": 5, "subscription_status": "active"},
            source="webhook:subscription.updated",
        )

        assert db.add.call_count == 2
        audit_fields = {db.add.call_args_list[i][0][0].field_name for i in range(2)}
        assert audit_fields == {"quantity", "subscription_status"}

    @pytest.mark.asyncio
    async def test_serialises_datetime_in_audit_old_value(self):
        dt = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        state = _make_state(current_period_start=dt)
        db = _make_db(state)

        new_dt = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
        await BillingStateService.update(
            db,
            updates={"current_period_start": new_dt},
            source="webhook:subscription.updated",
        )

        audit_row = db.add.call_args[0][0]
        # old_value must be JSON-serialisable (ISO string, not a datetime)
        assert isinstance(audit_row.old_value, str)
        assert audit_row.old_value == dt.isoformat()

    @pytest.mark.asyncio
    async def test_raises_when_singleton_missing(self):
        """Should raise RuntimeError when the billing_state row doesn't exist."""
        db = _make_db(None)

        with pytest.raises(RuntimeError, match="billing_state singleton row missing"):
            await BillingStateService.update(
                db,
                updates={"quantity": 5},
                source="webhook:subscription.updated",
            )

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_unknown_fields_with_warning(self):
        state = _make_state(quantity=1)
        db = _make_db(state)

        with patch("shu.billing.state_service.logger") as mock_log:
            result = await BillingStateService.update(
                db,
                updates={"nonexistent_column": "value", "quantity": 5},
                source="test",
            )

        assert result is state
        assert state.quantity == 5  # known field still applied
        mock_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_raises_on_db_exception(self):
        """DB exceptions should propagate to the caller."""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("DB exploded"))

        with pytest.raises(RuntimeError, match="DB exploded"):
            await BillingStateService.update(
                db,
                updates={"quantity": 5},
                source="webhook:subscription.updated",
            )

    @pytest.mark.asyncio
    async def test_updates_updated_at_timestamp(self):
        old_ts = datetime(2026, 1, 1, tzinfo=UTC)
        state = _make_state(updated_at=old_ts)
        db = _make_db(state)

        await BillingStateService.update(
            db,
            updates={"quantity": 3},
            source="test",
        )

        assert state.updated_at > old_ts


# ---------------------------------------------------------------------------
# Concurrent update scenario
# ---------------------------------------------------------------------------


class TestConcurrentUpdates:
    """Two handlers updating different fields must both succeed (no lost write).

    This test verifies the *logic* — that two separate update() calls each
    apply their respective fields. Serialisation at the DB level (the
    SELECT FOR UPDATE lock) is proven by integration tests.
    """

    @pytest.mark.asyncio
    async def test_two_handlers_updating_different_fields_both_persist(self):
        # Shared mutable state — both "sessions" see and update the same object
        state = _make_state(quantity=3, subscription_status="pending")

        db_a = _make_db(state)
        db_b = _make_db(state)

        # Run both coroutines (would be serialised by FOR UPDATE in real DB)
        await asyncio.gather(
            BillingStateService.update(
                db_a,
                updates={"quantity": 10},
                source="webhook:subscription.updated",
            ),
            BillingStateService.update(
                db_b,
                updates={"subscription_status": "active"},
                source="webhook:subscription.created",
            ),
        )

        # Both fields should reflect their respective updates
        assert state.quantity == 10
        assert state.subscription_status == "active"


# ---------------------------------------------------------------------------
# BillingStateService.seed_from_config
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal settings stub for seed_from_config tests."""

    def __init__(self, customer_id: str | None = None, subscription_id: str | None = None) -> None:
        self.customer_id = customer_id
        self.subscription_id = subscription_id


class TestSeedFromConfig:
    @pytest.mark.asyncio
    async def test_seeds_both_fields_when_both_null(self):
        state = _make_state(stripe_customer_id=None, stripe_subscription_id=None)
        db = _make_db(state)
        settings = _FakeSettings(customer_id="cus_abc", subscription_id="sub_xyz")

        await BillingStateService.seed_from_config(db, settings)

        assert state.stripe_customer_id == "cus_abc"
        assert state.stripe_subscription_id == "sub_xyz"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_customer_id(self):
        state = _make_state(stripe_customer_id="cus_existing", stripe_subscription_id=None)
        db = _make_db(state)
        settings = _FakeSettings(customer_id="cus_new", subscription_id="sub_xyz")

        await BillingStateService.seed_from_config(db, settings)

        # customer_id must not be overwritten; subscription_id should be written
        assert state.stripe_customer_id == "cus_existing"
        assert state.stripe_subscription_id == "sub_xyz"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_subscription_id(self):
        state = _make_state(stripe_customer_id=None, stripe_subscription_id="sub_existing")
        db = _make_db(state)
        settings = _FakeSettings(customer_id="cus_abc", subscription_id="sub_new")

        await BillingStateService.seed_from_config(db, settings)

        assert state.stripe_customer_id == "cus_abc"
        assert state.stripe_subscription_id == "sub_existing"

    @pytest.mark.asyncio
    async def test_noop_when_both_fields_already_set(self):
        state = _make_state(stripe_customer_id="cus_existing", stripe_subscription_id="sub_existing")
        db = _make_db(state)
        settings = _FakeSettings(customer_id="cus_new", subscription_id="sub_new")

        await BillingStateService.seed_from_config(db, settings)

        assert state.stripe_customer_id == "cus_existing"
        assert state.stripe_subscription_id == "sub_existing"
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_when_no_env_vars_set(self):
        state = _make_state(stripe_customer_id=None, stripe_subscription_id=None)
        db = _make_db(state)
        settings = _FakeSettings(customer_id=None, subscription_id=None)

        await BillingStateService.seed_from_config(db, settings)

        # Nothing to seed — no commit should happen
        assert state.stripe_customer_id is None
        assert state.stripe_subscription_id is None
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_when_singleton_row_missing(self):
        """seed_from_config silently skips if billing_state not yet created."""
        db = _make_db(None)
        settings = _FakeSettings(customer_id="cus_abc", subscription_id="sub_xyz")

        # Should not raise, even with a missing row
        await BillingStateService.seed_from_config(db, settings)

        db.commit.assert_not_awaited()
