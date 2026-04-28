"""Tests for billing adapters — usage queries, persistence callbacks, billing config."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.adapters import (
    UsageProviderImpl,
    create_cycle_rollover_callback,
    create_payment_failed_callback,
    create_subscription_persistence_callback,
    get_billing_config,
    get_user_count,
)
from shu.billing.schemas import SubscriptionUpdate
from shu.models.billing_state import BillingState


def _make_billing_state(**kwargs) -> BillingState:
    state = BillingState()
    state.id = 1
    state.stripe_customer_id = kwargs.get("stripe_customer_id", "cus_123")
    state.stripe_subscription_id = kwargs.get("stripe_subscription_id", "sub_456")
    state.billing_email = kwargs.get("billing_email", "billing@example.com")
    state.subscription_status = kwargs.get("subscription_status", "active")
    state.current_period_start = kwargs.get("current_period_start", datetime(2026, 4, 1, tzinfo=UTC))
    state.current_period_end = kwargs.get("current_period_end", datetime(2026, 5, 1, tzinfo=UTC))
    state.cancel_at_period_end = kwargs.get("cancel_at_period_end", False)
    state.last_reported_total = kwargs.get("last_reported_total", 0)
    state.last_reported_period_start = kwargs.get("last_reported_period_start", None)
    state.payment_failed_at = kwargs.get("payment_failed_at", None)
    state.user_limit_enforcement = kwargs.get("user_limit_enforcement", "soft")
    return state


class TestGetBillingConfig:
    """Tests for get_billing_config — reads from billing_state."""

    @pytest.mark.asyncio
    async def test_returns_dict_built_from_billing_state(self):
        """Should read from billing_state and return a dict with all expected keys."""
        mock_db = AsyncMock()
        state = _make_billing_state(
            stripe_customer_id="cus_123",
            subscription_status="active",
        )

        with patch("shu.billing.state_service.BillingStateService.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = state
            result = await get_billing_config(mock_db)

        assert result["stripe_customer_id"] == "cus_123"
        assert result["subscription_status"] == "active"
        assert result["current_period_start"] == "2026-04-01T00:00:00+00:00"
        assert result["current_period_end"] == "2026-05-01T00:00:00+00:00"
        assert "quantity" not in result
        assert "target_quantity" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_singleton(self):
        """Should return empty dict when billing_state row doesn't exist."""
        mock_db = AsyncMock()

        with patch("shu.billing.state_service.BillingStateService.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = await get_billing_config(mock_db)

        assert result == {}

    @pytest.mark.asyncio
    async def test_serialises_none_datetimes_as_none(self):
        """Datetime fields that are None should remain None in the dict."""
        mock_db = AsyncMock()
        state = _make_billing_state(
            current_period_start=None,
            current_period_end=None,
            last_reported_period_start=None,
        )

        with patch("shu.billing.state_service.BillingStateService.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = state
            result = await get_billing_config(mock_db)

        assert result["current_period_start"] is None
        assert result["current_period_end"] is None
        assert result["last_reported_period_start"] is None


class TestGetUserCount:
    """Tests for get_user_count."""

    @pytest.mark.asyncio
    async def test_returns_count(self):
        """Should return the user count from the DB."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 7
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_user_count(mock_db)

        assert result == 7

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_users(self):
        """Should return 0 when scalar returns None."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_user_count(mock_db)

        assert result == 0


class TestSubscriptionPersistenceCallback:
    """Tests for create_subscription_persistence_callback."""

    @pytest.mark.asyncio
    async def test_persists_subscription_update_to_billing_state(self):
        """Should call BillingStateService.update with the correct fields."""
        mock_db = AsyncMock()
        period_start = datetime(2026, 4, 1, tzinfo=UTC)
        period_end = datetime(2026, 5, 1, tzinfo=UTC)

        update = SubscriptionUpdate(
            stripe_subscription_id="sub_123",
            stripe_customer_id="cus_456",
            status="active",
            quantity=5,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=False,
        )

        with patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_update:
            persist_fn = await create_subscription_persistence_callback(mock_db)
            await persist_fn(update, stripe_event_id="evt_abc")

        mock_update.assert_awaited_once()
        call_kwargs = mock_update.call_args
        updates = call_kwargs.kwargs["updates"] if call_kwargs.kwargs else call_kwargs[1]["updates"]
        assert updates["stripe_subscription_id"] == "sub_123"
        assert updates["stripe_customer_id"] == "cus_456"
        assert updates["subscription_status"] == "active"
        assert "quantity" not in updates
        assert "target_quantity" not in updates
        assert updates["current_period_start"] == period_start
        assert updates["current_period_end"] == period_end

    @pytest.mark.asyncio
    async def test_passes_stripe_event_id(self):
        """stripe_event_id should be forwarded to BillingStateService.update."""
        mock_db = AsyncMock()
        update = SubscriptionUpdate(
            stripe_subscription_id="sub_new",
            stripe_customer_id="cus_new",
            status="trialing",
            quantity=1,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )

        with patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_update:
            persist_fn = await create_subscription_persistence_callback(mock_db)
            await persist_fn(update, stripe_event_id="evt_xyz")

        _, kwargs = mock_update.call_args
        assert kwargs["stripe_event_id"] == "evt_xyz"


class TestPaymentFailedCallback:
    """Tests for create_payment_failed_callback — grace-period idempotency."""

    @pytest.mark.asyncio
    async def test_sets_payment_failed_at_on_first_failure(self):
        """First invoice.payment_failed must write payment_failed_at."""
        state = _make_billing_state(payment_failed_at=None)
        mock_db = AsyncMock()

        with (
            patch("shu.billing.state_service.BillingStateService.get", new_callable=AsyncMock, return_value=state),
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_update,
        ):
            cb = await create_payment_failed_callback(mock_db)
            await cb("cus_123", "sub_456", "in_789", stripe_event_id="evt_abc")

        mock_update.assert_awaited_once()
        updates = mock_update.call_args.kwargs["updates"]
        assert updates["payment_failed_at"] is not None

    @pytest.mark.asyncio
    async def test_preserves_first_timestamp_on_dunning_retry(self):
        """Subsequent invoice.payment_failed events must not overwrite payment_failed_at.

        Stripe sends one event per dunning retry (e.g. day 1, day 4, day 7).
        Each retry must preserve the original grace-period start so enforcement
        is not postponed indefinitely.
        """
        first_failure = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        state = _make_billing_state(payment_failed_at=first_failure)
        mock_db = AsyncMock()

        with (
            patch("shu.billing.state_service.BillingStateService.get", new_callable=AsyncMock, return_value=state),
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_update,
        ):
            cb = await create_payment_failed_callback(mock_db)
            # Simulate a second dunning retry firing days later
            await cb("cus_123", "sub_456", "in_789_retry2", stripe_event_id="evt_retry2")

        mock_update.assert_not_awaited()


class TestUsageProviderImpl:
    """Tests for UsageProviderImpl — usage aggregation queries."""

    @pytest.mark.asyncio
    async def test_get_usage_summary_aggregates_by_model(self):
        """Should aggregate usage by model and compute totals as Decimal."""
        from decimal import Decimal

        mock_db = AsyncMock()

        # DB returns Decimal for DECIMAL(16,9) columns; preserve that all the way through.
        row1 = MagicMock()
        row1.model_id = "claude-haiku-4-5"
        row1.input_tokens = 1000
        row1.output_tokens = 200
        row1.total_cost = Decimal("1.500000000")
        row1.request_count = 10

        row2 = MagicMock()
        row2.model_id = "gpt-5.4"
        row2.input_tokens = 500
        row2.output_tokens = 100
        row2.total_cost = Decimal("0.750000000")
        row2.request_count = 5

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([row1, row2]))
        mock_db.execute = AsyncMock(return_value=mock_result)

        provider = UsageProviderImpl(mock_db)
        summary = await provider.get_usage_summary(
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )

        assert summary.total_input_tokens == 1500
        assert summary.total_output_tokens == 300
        assert summary.total_cost_usd == Decimal("2.250000000")
        # Verify Decimal type is preserved (not silently converted to float)
        assert isinstance(summary.total_cost_usd, Decimal)
        assert isinstance(summary.by_model["claude-haiku-4-5"].cost_usd, Decimal)
        assert len(summary.by_model) == 2
        assert summary.by_model["claude-haiku-4-5"].request_count == 10

    @pytest.mark.asyncio
    async def test_get_usage_summary_handles_empty_period(self):
        """Should return zeros when no usage exists in the period."""
        from decimal import Decimal

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_db.execute = AsyncMock(return_value=mock_result)

        provider = UsageProviderImpl(mock_db)
        summary = await provider.get_usage_summary(
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )

        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0
        assert summary.total_cost_usd == Decimal("0")
        assert len(summary.by_model) == 0

    @pytest.mark.asyncio
    async def test_handles_null_model_id(self):
        """Should map null model_id to 'unknown'."""
        from decimal import Decimal

        mock_db = AsyncMock()

        row = MagicMock()
        row.model_id = None
        row.input_tokens = 100
        row.output_tokens = 50
        row.total_cost = Decimal("0.100000000")
        row.request_count = 1

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([row]))
        mock_db.execute = AsyncMock(return_value=mock_result)

        provider = UsageProviderImpl(mock_db)
        summary = await provider.get_usage_summary(
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )

        assert "unknown" in summary.by_model

    @pytest.mark.asyncio
    async def test_get_usage_summary_filters_to_system_managed_providers(self):
        """SHU-705 billing correctness: aggregation MUST join llm_providers
        and restrict to is_system_managed=TRUE. BYOK rows belong to the
        customer, not Shu's invoice.
        """
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_db.execute = AsyncMock(return_value=mock_result)

        provider = UsageProviderImpl(mock_db)
        await provider.get_usage_summary(
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )

        stmt = mock_db.execute.call_args.args[0]
        compiled_sql = str(stmt.compile()).lower()
        assert "join llm_providers" in compiled_sql
        assert "is_system_managed" in compiled_sql

    @pytest.mark.asyncio
    async def test_get_usage_for_period_filters_to_system_managed_providers(self):
        """Per-record query mirrors the summary filter — same billing rule."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=iter([]))
        mock_db.execute = AsyncMock(return_value=mock_result)

        provider = UsageProviderImpl(mock_db)
        await provider.get_usage_for_period(
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )

        stmt = mock_db.execute.call_args.args[0]
        compiled_sql = str(stmt.compile()).lower()
        assert "join llm_providers" in compiled_sql
        assert "is_system_managed" in compiled_sql


class TestCreateCycleRolloverCallback:
    """Callback wraps SeatService.rollover + filters on billing_reason."""

    @pytest.mark.asyncio
    async def test_invokes_rollover_when_billing_reason_is_subscription_cycle(self):
        mock_db = AsyncMock()
        seat_service = MagicMock()
        seat_service.rollover = AsyncMock()

        callback = create_cycle_rollover_callback(mock_db, seat_service)
        await callback("cus_1", "sub_1", "in_1", "evt_1", "subscription_cycle")

        seat_service.rollover.assert_awaited_once_with(mock_db, "sub_1", "evt_1")

    @pytest.mark.asyncio
    async def test_noop_when_billing_reason_is_anything_else(self):
        """subscription_create / subscription_update / manual → skip."""
        mock_db = AsyncMock()
        seat_service = MagicMock()
        seat_service.rollover = AsyncMock()

        callback = create_cycle_rollover_callback(mock_db, seat_service)
        for reason in ("subscription_create", "subscription_update", "manual", None):
            await callback("cus_1", "sub_1", "in_1", "evt_1", reason)

        seat_service.rollover.assert_not_awaited()
