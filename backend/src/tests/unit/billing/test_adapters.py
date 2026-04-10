"""Tests for billing adapters — usage queries, persistence callbacks, billing config."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.adapters import (
    BILLING_SETTINGS_KEY,
    UsageProviderImpl,
    create_customer_link_callback,
    create_subscription_persistence_callback,
    get_billing_config,
    get_user_count,
)
from shu.billing.schemas import SubscriptionUpdate


class TestGetBillingConfig:
    """Tests for get_billing_config — system_settings reader."""

    @pytest.mark.asyncio
    async def test_returns_billing_config(self):
        """Should read from system_settings and return the dict."""
        mock_db = AsyncMock()
        expected = {"stripe_customer_id": "cus_123", "subscription_status": "active"}

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value=expected)
            mock_ss_cls.return_value = mock_ss

            result = await get_billing_config(mock_db)

        assert result == expected
        mock_ss.get_value.assert_awaited_once_with(BILLING_SETTINGS_KEY, {})

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_config(self):
        """Should return empty dict when no billing config exists."""
        mock_db = AsyncMock()

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value=None)
            mock_ss_cls.return_value = mock_ss

            result = await get_billing_config(mock_db)

        assert result == {}


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
    async def test_persists_subscription_update(self):
        """Should merge subscription data into system_settings."""
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

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value={"billing_email": "existing@test.com"})
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            persist_fn = await create_subscription_persistence_callback(mock_db)
            await persist_fn(update)

            mock_ss.upsert.assert_awaited_once()
            saved_config = mock_ss.upsert.call_args[0][1]

            # Verify subscription fields were merged
            assert saved_config["stripe_subscription_id"] == "sub_123"
            assert saved_config["stripe_customer_id"] == "cus_456"
            assert saved_config["subscription_status"] == "active"
            assert saved_config["quantity"] == 5
            # Verify existing data preserved
            assert saved_config["billing_email"] == "existing@test.com"

    @pytest.mark.asyncio
    async def test_handles_empty_existing_config(self):
        """Should work when no billing config exists yet."""
        mock_db = AsyncMock()
        update = SubscriptionUpdate(
            stripe_subscription_id="sub_new",
            stripe_customer_id="cus_new",
            status="trialing",
            quantity=1,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value=None)
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            persist_fn = await create_subscription_persistence_callback(mock_db)
            await persist_fn(update)

            mock_ss.upsert.assert_awaited_once()
            saved_config = mock_ss.upsert.call_args[0][1]
            assert saved_config["stripe_subscription_id"] == "sub_new"


class TestCustomerLinkCallback:
    """Tests for create_customer_link_callback."""

    @pytest.mark.asyncio
    async def test_links_customer_with_subscription(self):
        """Should store customer ID, email, and subscription ID."""
        mock_db = AsyncMock()

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value={})
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            link_fn = await create_customer_link_callback(mock_db)
            result = await link_fn("cus_123", "billing@test.com", "sub_456")

            assert result is True
            saved_config = mock_ss.upsert.call_args[0][1]
            assert saved_config["stripe_customer_id"] == "cus_123"
            assert saved_config["billing_email"] == "billing@test.com"
            assert saved_config["stripe_subscription_id"] == "sub_456"

    @pytest.mark.asyncio
    async def test_links_customer_without_subscription(self):
        """Should store customer ID and email without overwriting subscription."""
        mock_db = AsyncMock()

        with patch("shu.services.system_settings_service.SystemSettingsService") as mock_ss_cls:
            mock_ss = MagicMock()
            mock_ss.get_value = AsyncMock(return_value={"stripe_subscription_id": "sub_existing"})
            mock_ss.upsert = AsyncMock()
            mock_ss_cls.return_value = mock_ss

            link_fn = await create_customer_link_callback(mock_db)
            result = await link_fn("cus_123", "billing@test.com", None)

            assert result is True
            saved_config = mock_ss.upsert.call_args[0][1]
            assert saved_config["stripe_customer_id"] == "cus_123"
            # subscription_id should not be overwritten
            assert saved_config["stripe_subscription_id"] == "sub_existing"


class TestUsageProviderImpl:
    """Tests for UsageProviderImpl — usage aggregation queries."""

    @pytest.mark.asyncio
    async def test_get_usage_summary_aggregates_by_model(self):
        """Should aggregate usage by model and compute totals."""
        mock_db = AsyncMock()

        # Simulate two rows from the GROUP BY query
        row1 = MagicMock()
        row1.model_id = "claude-haiku-4-5"
        row1.input_tokens = 1000
        row1.output_tokens = 200
        row1.total_cost = 1.50
        row1.request_count = 10

        row2 = MagicMock()
        row2.model_id = "gpt-5.4"
        row2.input_tokens = 500
        row2.output_tokens = 100
        row2.total_cost = 0.75
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
        assert summary.total_cost_usd == 2.25
        assert len(summary.by_model) == 2
        assert summary.by_model["claude-haiku-4-5"].request_count == 10
        assert summary.by_model["gpt-5.4"].cost_usd == 0.75

    @pytest.mark.asyncio
    async def test_get_usage_summary_handles_empty_period(self):
        """Should return zeros when no usage exists in the period."""
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
        assert summary.total_cost_usd == 0.0
        assert len(summary.by_model) == 0

    @pytest.mark.asyncio
    async def test_handles_null_model_id(self):
        """Should map null model_id to 'unknown'."""
        mock_db = AsyncMock()

        row = MagicMock()
        row.model_id = None
        row.input_tokens = 100
        row.output_tokens = 50
        row.total_cost = 0.10
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
