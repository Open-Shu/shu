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
    return settings


def _make_client():
    client = MagicMock()
    # Sync methods (StripeClient methods are sync)
    client.get_subscription = MagicMock()
    client.update_subscription_quantity = MagicMock()
    client.create_checkout_session = MagicMock()
    client.create_portal_session = MagicMock()
    client.create_customer = MagicMock()
    client.report_usage = MagicMock()
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
            "items": {"data": [{"id": "si_1", "quantity": 3}]},
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
            "items": {"data": [{"id": "si_1", "quantity": 5}]},
        }
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_123", user_count=5)

        assert result is False
        client.update_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_subscription_not_found(self):
        """Should return False when Stripe subscription doesn't exist."""
        client = _make_client()
        client.get_subscription.return_value = None
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_gone", user_count=5)

        assert result is False
        client.update_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_empty_items_data(self):
        """Should treat empty items.data as quantity 0."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {"data": []},
        }
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.sync_subscription_quantity("sub_123", user_count=3)

        assert result is True
        client.update_subscription_quantity.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_stripe_error(self):
        """Should propagate StripeClientError from update call."""
        client = _make_client()
        client.get_subscription.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 3}]},
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

    @pytest.mark.asyncio
    async def test_allows_event_when_no_expected_customer(self):
        """When no customer is linked yet, all events should be allowed."""
        client = _make_client()
        event = MagicMock()
        event.type = "checkout.session.completed"
        event.id = "evt_new"
        event.data.object = {"customer": "cus_new", "id": "cs_123"}
        client.construct_webhook_event.return_value = event

        service = BillingService(_make_settings(), stripe_client=client)

        handled, event_type, event_id = await service.handle_webhook(
            payload=b"payload",
            signature="sig",
            expected_customer_id=None,  # No customer linked yet
        )

        # Should process (not reject) — handled depends on dispatcher having a handler
        assert event_type == "checkout.session.completed"


class TestReportUsageToStripe:
    """Tests for usage delta reporting contract."""

    @pytest.mark.asyncio
    async def test_skips_when_zero_tokens(self):
        """Should not call Stripe when delta is zero."""
        client = _make_client()
        service = BillingService(_make_settings(), stripe_client=client)

        result = await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_tokens=0,
            period_start=MagicMock(),
            period_end=MagicMock(),
        )

        assert result is True
        client.report_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_nonzero_delta(self):
        """Should call Stripe with the token delta."""
        from datetime import UTC, datetime

        client = _make_client()
        client.report_usage.return_value = MagicMock()  # Non-None = success
        service = BillingService(_make_settings(), stripe_client=client)

        period_start = datetime(2026, 4, 1, tzinfo=UTC)
        period_end = datetime(2026, 4, 2, tzinfo=UTC)

        result = await service.report_usage_to_stripe(
            stripe_customer_id="cus_123",
            delta_tokens=5000,
            period_start=period_start,
            period_end=period_end,
            input_tokens=3000,
            output_tokens=2000,
        )

        assert result is True
        client.report_usage.assert_called_once()
        event_arg = client.report_usage.call_args[0][0]
        assert event_arg.value == 5000
        assert event_arg.stripe_customer_id == "cus_123"


def _make_usage_summary(input_tokens=0, output_tokens=0):
    """Create a mock UsageSummary."""
    summary = MagicMock()
    summary.total_input_tokens = input_tokens
    summary.total_output_tokens = output_tokens
    summary.total_cost_usd = 0.0
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
        settings.meter_id_tokens = None
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
        """our=50k, stripe=45k → send 5k delta."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 45000
        client.report_usage.return_value = MagicMock()  # Success

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
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
            patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(input_tokens=30000, output_tokens=20000)
            )
            mock_provider_cls.return_value = mock_provider

            mock_ss = MagicMock()
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 5000  # 50000 - 45000
        assert result["our_total"] == 50000
        assert result["stripe_total"] == 45000
        client.report_usage.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_delta_when_totals_match(self):
        """our=50k, stripe=50k → no delta needed."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 50000

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
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
                return_value=_make_usage_summary(input_tokens=30000, output_tokens=20000)
            )
            mock_provider_cls.return_value = mock_provider

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "no_delta"
        client.report_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_lag_uses_last_reported(self):
        """our=53k, stripe=45k (lag), last_reported=50k → send 3k not 8k."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 45000  # Stripe hasn't caught up
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
            "last_reported_total": 50000,  # We reported 50k last run
            "last_reported_period_start": "2026-04-01T00:00:00+00:00",
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(input_tokens=33000, output_tokens=20000)
            )
            mock_provider_cls.return_value = mock_provider

            mock_ss = MagicMock()
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 3000  # 53000 - 50000 (not 53000 - 45000)

    @pytest.mark.asyncio
    async def test_first_report_no_last_reported(self):
        """First run with no last_reported_total → reports full amount."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 0  # Nothing in Stripe
        client.report_usage.return_value = MagicMock()

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
        service = BillingService(settings, stripe_client=client)
        db = AsyncMock()

        billing_config = {
            "stripe_customer_id": "cus_123",
            "current_period_start": "2026-04-01T00:00:00+00:00",
            "current_period_end": "2026-05-01T00:00:00+00:00",
            # No last_reported_total or last_reported_period_start
        }

        with (
            patch("shu.billing.adapters.get_billing_config") as mock_config,
            patch("shu.billing.adapters.UsageProviderImpl") as mock_provider_cls,
            patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(input_tokens=10000, output_tokens=5000)
            )
            mock_provider_cls.return_value = mock_provider

            mock_ss = MagicMock()
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["delta"] == 15000  # Full amount

    @pytest.mark.asyncio
    async def test_does_not_update_on_report_failure(self):
        """Should not update last_reported_total if Stripe report fails."""
        client = _make_client()
        client.get_meter_event_summary.return_value = 0
        client.report_usage.return_value = None  # Failed

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
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
            patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                return_value=_make_usage_summary(input_tokens=10000, output_tokens=5000)
            )
            mock_provider_cls.return_value = mock_provider

            mock_ss = MagicMock()
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "report_failed"
        mock_ss.upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_period_rollover_catchup(self):
        """Should catchup old period gap before reporting new period usage."""
        client = _make_client()
        # Old period: DB has 50k tokens, Stripe has 40k, we last reported 45k.
        # Gap = 50k - max(40k, 45k) = 50k - 45k = 5k catchup needed.
        # New period: we have 3k, Stripe has 0.
        client.get_meter_event_summary.side_effect = [
            40000,  # Old period Stripe query
            0,      # New period Stripe query
        ]
        client.report_usage.return_value = MagicMock()  # Success

        settings = _make_settings()
        settings.meter_id_tokens = "meter_123"
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
            patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls,
        ):
            mock_config.return_value = billing_config
            mock_provider = MagicMock()
            mock_provider.get_usage_summary = AsyncMock(
                side_effect=[
                    _make_usage_summary(input_tokens=30000, output_tokens=20000),  # Old: 50k (gap exists)
                    _make_usage_summary(input_tokens=2000, output_tokens=1000),    # New: 3k
                ]
            )
            mock_provider_cls.return_value = mock_provider

            mock_ss = MagicMock()
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            result = await service.report_and_reconcile_usage(db)

        assert result["action"] == "reported"
        assert result["our_total"] == 3000
        # report_usage called twice: 5k old period catchup + 3k new period
        assert client.report_usage.call_count == 2
