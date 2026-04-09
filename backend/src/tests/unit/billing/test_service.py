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
