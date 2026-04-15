"""Tests for BillingService — orchestration, customer scoping, quantity sync."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.schemas import SubscriptionUpdate
from shu.billing.service import BillingService
from shu.billing.stripe_client import StripeClientError


def _make_settings():
    settings = MagicMock()
    settings.is_configured = True
    settings.app_base_url = "http://localhost:3000"
    settings.meter_id_cost = None
    settings.meter_event_name = "usage_cost"
    settings.price_id_monthly = "price_seat"
    return settings


def _make_client():
    client = MagicMock()
    # Async I/O methods
    client.get_subscription = AsyncMock()
    client.update_subscription_quantity = AsyncMock()
    client.create_portal_session = AsyncMock()
    client.create_customer = AsyncMock()
    client.report_usage = AsyncMock()
    client.get_meter_event_summary = AsyncMock()
    # Sync methods (no network I/O)
    client.construct_webhook_event = MagicMock()
    client.parse_subscription_update = MagicMock()
    return client


class TestSyncSubscriptionQuantity:
    """Tests for BillingService.sync_subscription_quantity."""

    @pytest.mark.asyncio
    async def test_updates_when_quantity_differs(self):
        """Should call Stripe when user count differs from subscription quantity."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 3, "price": {"id": "price_seat"}}]},
        }
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_123", user_count=5)

        assert result is True
        client.update_subscription_quantity.assert_called_once_with(
            "sub_123", 5, "create_prorations"
        )

    @pytest.mark.asyncio
    async def test_skips_when_quantity_matches(self):
        """Should not call Stripe when quantity already matches."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 5, "price": {"id": "price_seat"}}]},
        }
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_123", user_count=5)

        assert result is False
        client.update_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_subscription_not_found(self):
        """Should raise StripeClientError when Stripe subscription doesn't exist.

        Callers must not persist local quantity on raise — the subscription
        state is unknown and writing user_count would produce wrong quota data.
        """
        client = _make_client()
        client.get_subscription.return_value = None
        service = BillingService(_make_settings(), stripe_client=client)

        with pytest.raises(StripeClientError):
            await service.sync_subscription_quantity("sub_gone", user_count=5)

        client.update_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_no_seat_item_found(self):
        """Should raise StripeClientError when no item matches the configured seat price.

        Falling back to items[0] on a mixed subscription (seat + metered) would
        persist the metered item's quantity, which is wrong.
        """
        client = _make_client()
        # No items at all
        client.get_subscription.return_value = {"items": {"data": []}}
        service = BillingService(_make_settings(), stripe_client=client)

        with pytest.raises(StripeClientError):
            await service.sync_subscription_quantity("sub_123", user_count=3)

        client.update_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_picks_seat_item_among_multiple(self):
        """With seat + metered items, should match by price ID, not pick index 0."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {
                "data": [
                    # Metered item first — quantity is meaningless here
                    {"id": "si_meter", "quantity": 1, "price": {"id": "price_metered"}},
                    {"id": "si_seat", "quantity": 3, "price": {"id": "price_seat"}},
                ]
            },
        }
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_123", user_count=5)

        # Should pick the seat item, see quantity 3 ≠ 5, and update
        assert result is True
        client.update_subscription_quantity.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_stripe_error(self):
        """Should propagate StripeClientError from update call."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 3, "price": {"id": "price_seat"}}]},
        }
        client.update_subscription_quantity.side_effect = StripeClientError("API error")
        service = BillingService(_make_settings(), stripe_client=client)

        with pytest.raises(StripeClientError):
            await service.sync_subscription_quantity("sub_123", user_count=5)


class TestWebhookCustomerScoping:
    """Tests for _extract_customer_id and webhook scoping logic."""

    def test_extract_customer_from_subscription_event(self):
        """Should extract customer ID from subscription event data.object."""
        event = MagicMock()
        event.data.object = {"customer": "cus_abc", "id": "sub_123"}

        result = BillingService._extract_customer_id(event)

        assert result == "cus_abc"

    def test_extract_customer_from_customer_event(self):
        """Should extract customer ID from customer.* event where object IS the customer."""
        event = MagicMock()
        event.data.object = {"object": "customer", "id": "cus_xyz", "email": "test@example.com"}

        result = BillingService._extract_customer_id(event)

        assert result == "cus_xyz"

    def test_extract_customer_from_invoice_event(self):
        """Should extract customer from invoice events."""
        event = MagicMock()
        event.data.object = {"customer": "cus_inv", "id": "in_123"}

        result = BillingService._extract_customer_id(event)

        assert result == "cus_inv"

    def test_returns_none_when_no_customer(self):
        """Should return None when event has no customer field."""
        event = MagicMock()
        event.data.object = {"id": "evt_123"}

        result = BillingService._extract_customer_id(event)

        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_event_for_wrong_customer(self):
        """Webhook for a different customer should be ignored."""
        client = _make_client()
        event = MagicMock()
        event.type = "customer.subscription.updated"
        event.id = "evt_123"
        event.data.object = {"customer": "cus_other", "id": "sub_other"}
        client.construct_webhook_event.return_value = event

        service = BillingService(_make_settings(), stripe_client=client)

        handled, event_type, event_id = await service.handle_webhook(
            payload=b"payload",
            signature="sig",
            expected_customer_id="cus_mine",
        )

        assert handled is False
        assert event_type == "customer.subscription.updated"

    @pytest.mark.asyncio
    async def test_accepts_event_for_matching_customer(self):
        """Webhook for the correct customer should be processed."""
        client = _make_client()
        event = MagicMock()
        event.type = "customer.subscription.updated"
        event.id = "evt_123"
        event.data.object = {"customer": "cus_mine", "id": "sub_123"}
        client.construct_webhook_event.return_value = event
        client.parse_subscription_update.return_value = SubscriptionUpdate(
            stripe_subscription_id="sub_123",
            stripe_customer_id="cus_mine",
            status="active",
            quantity=5,
            current_period_start=MagicMock(),
            current_period_end=MagicMock(),
        )

        persist_sub = AsyncMock()
        service = BillingService(_make_settings(), stripe_client=client)

        handled, event_type, event_id = await service.handle_webhook(
            payload=b"payload",
            signature="sig",
            persist_subscription=persist_sub,
            expected_customer_id="cus_mine",
        )

        assert handled is True
        persist_sub.assert_awaited_once()

class TestReportUsageToStripe:
    """Tests for cost delta reporting contract."""

    @pytest.mark.asyncio
    async def test_skips_when_zero_cost(self):
        """Should not call Stripe when delta is zero."""
        client = _make_client()
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=0,
            period_start=MagicMock(),
            period_end=MagicMock(),
            cumulative_total_microdollars=0,
        )

        assert result is True
        client.report_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_nonzero_cost_delta(self):
        """Should call Stripe with cost in microdollars and a deterministic identifier."""
        from datetime import UTC, datetime

        client = _make_client()
        client.report_usage.return_value = MagicMock()  # Non-None = success
        service = BillingService(_make_settings(), stripe_client=client)

        period_start = datetime(2026, 4, 1, tzinfo=UTC)
        period_end = datetime(2026, 4, 2, tzinfo=UTC)

        result = await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=5000000,  # $5.00
            period_start=period_start,
            period_end=period_end,
            cumulative_total_microdollars=5000000,
        )

        assert result is True
        client.report_usage.assert_called_once()
        event_arg = client.report_usage.call_args[0][0]
        assert event_arg.value == 5000000
        assert event_arg.stripe_customer_id == "cus_123"
        assert event_arg.event_name == "usage_cost"
        # Identifier encodes the cumulative position so retries dedupe naturally
        assert event_arg.identifier == f"shu-usage-cus_123-{int(period_start.timestamp())}-5000000"

    @pytest.mark.asyncio
    async def test_identifier_is_deterministic_for_same_cumulative(self):
        """Two calls with the same cumulative position produce the same identifier."""
        from datetime import UTC, datetime

        client = _make_client()
        client.report_usage.return_value = MagicMock()
        service = BillingService(_make_settings(), stripe_client=client)

        period_start = datetime(2026, 4, 1, tzinfo=UTC)
        period_end = datetime(2026, 4, 2, tzinfo=UTC)

        await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=1000,
            period_start=period_start,
            period_end=period_end,
            cumulative_total_microdollars=10000,
        )
        first_id = client.report_usage.call_args[0][0].identifier

        await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=1000,
            period_start=period_start,
            period_end=period_end,
            cumulative_total_microdollars=10000,  # Same cumulative — retry scenario
        )
        second_id = client.report_usage.call_args[0][0].identifier

        assert first_id == second_id

    @pytest.mark.asyncio
    async def test_identifier_changes_with_cumulative(self):
        """New cumulative position produces a new identifier (genuinely new usage)."""
        from datetime import UTC, datetime

        client = _make_client()
        client.report_usage.return_value = MagicMock()
        service = BillingService(_make_settings(), stripe_client=client)

        period_start = datetime(2026, 4, 1, tzinfo=UTC)
        period_end = datetime(2026, 4, 2, tzinfo=UTC)

        await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=1000,
            period_start=period_start,
            period_end=period_end,
            cumulative_total_microdollars=10000,
        )
        first_id = client.report_usage.call_args[0][0].identifier

        await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_cost_microdollars=500,
            period_start=period_start,
            period_end=period_end,
            cumulative_total_microdollars=10500,  # New usage
        )
        second_id = client.report_usage.call_args[0][0].identifier

        assert first_id != second_id


def _make_usage_summary(total_cost_usd=0.0, input_tokens=0, output_tokens=0):
    """Create a mock UsageSummary."""
    from decimal import Decimal

    summary = MagicMock()
    summary.total_input_tokens = input_tokens
    summary.total_output_tokens = output_tokens
    summary.total_cost_usd = Decimal(str(total_cost_usd))
    summary.by_model = {}
    return summary


class TestReportAndReconcileUsage:
    """Tests for the compare-and-correct usage reconciliation algorithm."""

    @pytest.mark.asyncio
    async def test_skips_when_no_customer(self):
        """Should skip when no Stripe customer is linked."""
        client = _make_client()
        service = BillingService(_make_settings(), stripe_client=client)
        db = AsyncMock()

        with patch("shu.billing.adapters.get_billing_config") as mock_config:
            mock_config.return_value = {"stripe_subscription_id": "sub_123"}  # No customer_id

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "skipped"
        assert result["reason"] == "no_customer"

    @pytest.mark.asyncio
    async def test_skips_when_no_meter(self):
        """Should skip when meter is not configured."""
        settings = _make_settings()
        settings.meter_id_cost = None
        client = _make_client()
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        with patch("shu.billing.adapters.get_billing_config") as mock_config:
            mock_config.return_value = {"stripe_customer_id": "cus_123"}

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "skipped"
        assert result["reason"] == "no_meter"

    @pytest.mark.asyncio
    async def test_reports_normal_delta(self):
        """our=$0.05, stripe=45000 microdollars → send 5000 microdollar delta."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 45000  # Stripe has 45000 microdollars
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
            "last_reported_total": 45000,
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock),
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.05)  # $0.05 = 50000 microdollars
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 5000
        assert result["our_total"] == 50000
        assert result["stripe_total"] == 45000
        client.report_usage.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_delta_when_totals_match(self):
        """our=$0.05 = stripe=50000 microdollars → no delta."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 50000

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
            "last_reported_total": 50000,
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.05)
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "no_delta"
        client.report_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_lag_uses_last_reported(self):
        """our=$0.053, stripe=45000 (lag), last_reported=50000 → send 3000 not 8000."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 45000
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
            "last_reported_total": 50000,
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock),
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.053)  # 53000 microdollars
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 3000  # 53000 - 50000

    @pytest.mark.asyncio
    async def test_first_report_no_last_reported(self):
        """First run with no last_reported_total → reports full amount."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 0
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock),
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.015)  # 15000 microdollars
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 15000

    @pytest.mark.asyncio
    async def test_does_not_update_on_report_failure(self):
        """Should not update last_reported_total if Stripe report fails."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 0
        client.report_usage.return_value = None  # Failed

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_billing_update,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.015)
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "report_failed"
        mock_billing_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_period_rollover_catchup(self):
        """Should catchup old period gap before reporting new period usage."""
        client = _make_client()
        # Old period: DB cost=$0.05 (50000 usd), Stripe has 40000, last_reported=45000.
        # Gap = 50000 - max(40000, 45000) = 5000 catchup.
        # New period: cost=$0.003 (3000 usd), Stripe has 0.
        client.get_meter_event_summary.side_effect = [
            40000,  # Old period Stripe query
            0,      # New period Stripe query
        ]
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-05-01T00:00:00+00:00",
            "current_period_end": "2026-06-01T00:00:00+00:00",
            "last_reported_total": 45000,
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock),
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                side_effect=[
                    _make_usage_summary(total_cost_usd=0.05),   # Old: 50000 microdollars
                    _make_usage_summary(total_cost_usd=0.003),  # New: 3000 microdollars
                ]
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["our_total"] == 3000
        # report_usage called twice: old period catchup + new period
        assert client.report_usage.call_count == 2

    @pytest.mark.asyncio
    async def test_ceiling_rounding(self):
        """Fractional microdollars should round up, never down."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 0
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock),
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            # $0.0000015 = 1.5 microdollars → should ceil to 2
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.0000015)
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["our_total"] == 2  # ceil(1.5) = 2, not 1

    @pytest.mark.asyncio
    async def test_failed_catchup_preserves_old_period_marker(self):
        """If old-period catchup fails, must NOT reset last_reported_period_start.

        Otherwise future runs treat the new period as the only period and the
        old period's unreported usage is dropped permanently.
        """
        client = _make_client()
        # Old period: our=50000 microdollars, stripe=40000, last_reported=45000
        # → catchup delta = 50000 - max(40000, 45000) = 5000. Report FAILS.
        client.get_meter_event_summary.return_value = 40000
        client.report_usage.return_value = None  # Stripe report fails

        settings = _make_settings()
        settings.meter_id_cost = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-05-01T00:00:00+00:00",
            "current_period_end": "2026-06-01T00:00:00+00:00",
            "last_reported_total": 45000,
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock) as mock_bss_update,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(total_cost_usd=0.05)
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        # Caller should short-circuit — no new-period reporting
        assert result["action"] == "catchup_failed"
        # BillingStateService.update must NOT have been called (old period marker preserved)
        mock_bss_update.assert_not_awaited()


class TestWebhookGuard:
    """Webhook guard: misconfigured instances must drop all events."""

    @pytest.mark.asyncio
    async def test_missing_customer_id_drops_all_events(self):
        """When SHU_STRIPE_CUSTOMER_ID is not configured, all events must be dropped."""
        client = _make_client()
        event = MagicMock()
        event.type = "customer.subscription.updated"
        event.id = "evt_123"
        client.construct_webhook_event.return_value = event

        persist_sub = AsyncMock()
        service = BillingService(_make_settings(), stripe_client=client)

        handled, event_type, _ = await service.handle_webhook(
            payload=b"payload",
            signature="sig",
            persist_subscription=persist_sub,
            expected_customer_id=None,  # misconfigured — SHU_STRIPE_CUSTOMER_ID not set
        )

        assert handled is False
        assert event_type == "customer.subscription.updated"
        persist_sub.assert_not_awaited()
