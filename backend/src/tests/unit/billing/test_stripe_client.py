"""Tests for StripeClient — focuses on parsing/mapping at the Stripe boundary."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from shu.billing.schemas import SubscriptionUpdate
from shu.billing.stripe_client import StripeClient, StripeClientError, StripeConfigurationError


def _make_settings(**overrides):
    """Create a mock BillingSettings."""
    defaults = {
        "secret_key": "sk_test_fake",
        "publishable_key": "pk_test_fake",
        "webhook_secret": "whsec_fake",
        "price_id_monthly": "price_fake",
        "meter_id_tokens": None,
        "mode": "test",
        "app_base_url": "http://localhost:3000",
        "is_configured": True,
    }
    defaults.update(overrides)
    settings = MagicMock(**defaults)
    return settings


def _make_subscription_data(*, quantity=5, items_quantity=None, include_items=True):
    """Build a realistic Stripe subscription webhook payload.

    Args:
        quantity: The quantity to put on items.data[0] (canonical location).
        items_quantity: Override for items.data[0].quantity if different from quantity.
        include_items: Whether to include the items field at all.
    """
    data = {
        "id": "sub_test123",
        "customer": "cus_test456",
        "status": "active",
        "current_period_start": 1712016000,  # 2024-04-02T00:00:00Z
        "current_period_end": 1714694400,  # 2024-05-03T00:00:00Z
        "cancel_at_period_end": False,
        "canceled_at": None,
    }
    if include_items:
        data["items"] = {
            "data": [
                {
                    "id": "si_item1",
                    "price": {"id": "price_fake"},
                    "quantity": items_quantity if items_quantity is not None else quantity,
                }
            ]
        }
    return data


class TestParseSubscriptionUpdate:
    """Tests for StripeClient.parse_subscription_update — the webhook parsing boundary."""

    @patch("shu.billing.stripe_client.stripe")
    def test_extracts_quantity_from_items_data(self, mock_stripe):
        """Quantity must come from items.data[0].quantity, not subscription root."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data(quantity=7)

        result = client.parse_subscription_update(data)

        assert result.quantity == 7
        assert isinstance(result, SubscriptionUpdate)

    @patch("shu.billing.stripe_client.stripe")
    def test_quantity_defaults_to_1_when_items_missing(self, mock_stripe):
        """When items field is absent, quantity defaults to 1."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data(include_items=False)

        result = client.parse_subscription_update(data)

        assert result.quantity == 1

    @patch("shu.billing.stripe_client.stripe")
    def test_quantity_defaults_to_1_when_items_data_empty(self, mock_stripe):
        """When items.data is an empty list, quantity defaults to 1."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()
        data["items"]["data"] = []

        result = client.parse_subscription_update(data)

        assert result.quantity == 1

    @patch("shu.billing.stripe_client.stripe")
    def test_ignores_root_level_quantity(self, mock_stripe):
        """A root-level 'quantity' field (which Stripe doesn't send) must be ignored."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data(quantity=5)
        data["quantity"] = 999  # Bogus root-level field

        result = client.parse_subscription_update(data)

        assert result.quantity == 5  # From items.data[0], not root

    @patch("shu.billing.stripe_client.stripe")
    def test_parses_timestamps_as_utc(self, mock_stripe):
        """Period timestamps must be parsed as UTC datetimes."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.current_period_start.tzinfo is not None
        assert result.current_period_end.tzinfo is not None
        assert result.current_period_start < result.current_period_end

    @patch("shu.billing.stripe_client.stripe")
    def test_parses_canceled_at_when_present(self, mock_stripe):
        """canceled_at should be parsed when set."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()
        data["canceled_at"] = 1713000000
        data["cancel_at_period_end"] = True

        result = client.parse_subscription_update(data)

        assert result.canceled_at is not None
        assert result.cancel_at_period_end is True

    @patch("shu.billing.stripe_client.stripe")
    def test_canceled_at_none_when_not_set(self, mock_stripe):
        """canceled_at should be None when subscription isn't canceled."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.canceled_at is None

    @patch("shu.billing.stripe_client.stripe")
    def test_extracts_customer_and_subscription_ids(self, mock_stripe):
        """Core IDs must be extracted correctly."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.stripe_subscription_id == "sub_test123"
        assert result.stripe_customer_id == "cus_test456"
        assert result.status == "active"


class TestStripeClientInit:
    """Tests for StripeClient initialization and configuration validation."""

    @patch("shu.billing.stripe_client.stripe")
    def test_raises_when_secret_key_missing(self, mock_stripe):
        """Must raise StripeConfigurationError when no secret key."""
        with pytest.raises(StripeConfigurationError, match="secret key not configured"):
            StripeClient(_make_settings(secret_key=None))

    @patch("shu.billing.stripe_client.stripe")
    def test_sets_api_key(self, mock_stripe):
        """Should configure the stripe SDK with the secret key."""
        StripeClient(_make_settings(secret_key="sk_test_abc"))

        assert mock_stripe.api_key == "sk_test_abc"


class TestConstructWebhookEvent:
    """Tests for webhook signature verification."""

    @patch("shu.billing.stripe_client.stripe")
    def test_raises_when_webhook_secret_missing(self, mock_stripe):
        """Must raise when webhook secret is not configured."""
        client = StripeClient(_make_settings())
        client._settings.webhook_secret = None

        with pytest.raises(StripeConfigurationError, match="Webhook secret not configured"):
            client.construct_webhook_event(b"payload", "sig")

    @patch("shu.billing.stripe_client.stripe")
    def test_raises_on_invalid_signature(self, mock_stripe):
        """Must raise StripeClientError on signature verification failure."""
        import stripe as real_stripe

        mock_stripe.SignatureVerificationError = real_stripe.SignatureVerificationError
        mock_stripe.Webhook.construct_event.side_effect = real_stripe.SignatureVerificationError(
            "bad sig", "sig_header"
        )

        client = StripeClient(_make_settings())

        with pytest.raises(StripeClientError, match="Invalid webhook signature"):
            client.construct_webhook_event(b"payload", "bad_sig")
