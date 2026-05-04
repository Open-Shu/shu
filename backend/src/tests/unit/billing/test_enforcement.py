"""Tests for billing enforcement — user limit checking logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.enforcement import (
    SubscriptionInactiveError,
    UserLimitStatus,
    assert_subscription_active,
    check_user_limit,
    get_current_billing_state,
)

_P_BILLING_CONFIG = "shu.billing.enforcement.get_billing_config"
_P_USER_COUNT = "shu.billing.enforcement.get_user_count"


class TestCheckUserLimit:
    """Tests for check_user_limit — the core enforcement logic."""

    @pytest.mark.asyncio
    @patch(_P_BILLING_CONFIG)
    async def test_returns_none_enforcement_when_no_config(self, mock_config):
        """No billing config → enforcement='none', no limit."""
        mock_config.return_value = {}
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.enforcement == "none"
        assert result.at_limit is False

    @pytest.mark.asyncio
    @patch(_P_BILLING_CONFIG)
    async def test_returns_none_enforcement_when_no_subscription(self, mock_config):
        """Billing config exists but no subscription → enforcement='none'."""
        mock_config.return_value = {"stripe_customer_id": "cus_123"}
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.enforcement == "none"
        assert result.at_limit is False

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_returns_none_enforcement_when_quantity_zero(self, mock_config, mock_count):
        """quantity=0 → treat as unlimited."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 0,
        }
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.enforcement == "none"
        mock_count.assert_not_called()

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_at_limit_when_count_equals_quantity(self, mock_config, mock_count):
        """current_count == user_limit → at_limit=True."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 5,
            "user_limit_enforcement": "soft",
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.at_limit is True
        assert result.enforcement == "soft"
        assert result.current_count == 5
        assert result.user_limit == 5

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_at_limit_when_count_exceeds_quantity(self, mock_config, mock_count):
        """current_count > user_limit → at_limit=True."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 3,
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.at_limit is True
        assert result.enforcement == "hard"
        assert result.current_count == 5
        assert result.user_limit == 3

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_not_at_limit_when_count_below_quantity(self, mock_config, mock_count):
        """current_count < user_limit → at_limit=False."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 10,
        }
        mock_count.return_value = 3
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.at_limit is False
        assert result.enforcement == "soft"  # default
        assert result.current_count == 3
        assert result.user_limit == 10

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_defaults_enforcement_to_soft(self, mock_config, mock_count):
        """When user_limit_enforcement is not set, default to 'soft'."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 5,
            # No user_limit_enforcement key
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.enforcement == "soft"

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_hard_enforcement_from_config(self, mock_config, mock_count):
        """Should read enforcement mode from billing config."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "quantity": 5,
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 3
        db = AsyncMock()

        result = await check_user_limit(db)

        assert result.enforcement == "hard"
        assert result.at_limit is False  # Under limit


class TestUserLimitStatus:
    """Tests for the UserLimitStatus dataclass."""

    def test_is_frozen(self):
        """UserLimitStatus should be immutable."""
        s = UserLimitStatus(enforcement="soft", at_limit=True, current_count=5, user_limit=5)
        with pytest.raises(AttributeError):
            s.at_limit = False  # type: ignore[misc]


class TestAssertSubscriptionActive:
    """Tests for the SHU-703 subscription-active gate."""

    @pytest.mark.asyncio
    async def test_no_cache_does_not_raise(self, install_stub_cache):
        """Self-hosted bypass: cache is None → HEALTHY_DEFAULT → no raise.

        The fixture resets `_cache` on setup; we don't call `_install`, so
        the helper sees None and falls through to the healthy default.
        """
        await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_healthy_default_does_not_raise(self, install_stub_cache):
        """Cache returns the cold-start fallback → no raise."""
        install_stub_cache(HEALTHY_DEFAULT)
        await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_within_grace_does_not_raise(self, install_stub_cache):
        """Payment failed but key still active (within grace) → no raise.

        CP only flips `openrouter_key_disabled=True` after grace ends, so
        a populated `payment_failed_at` with the flag still false is the
        normal in-grace state.
        """
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=False,
                payment_failed_at=datetime(2026, 1, 1, tzinfo=UTC),
                payment_grace_days=7,
            )
        )
        await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_disabled_key_raises_with_computed_deadline(self, install_stub_cache):
        """Lockout state → raises with grace_deadline = failed_at + grace_days."""
        failed_at = datetime(2026, 1, 1, tzinfo=UTC)
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=True,
                payment_failed_at=failed_at,
                payment_grace_days=7,
            )
        )

        with pytest.raises(SubscriptionInactiveError) as exc_info:
            await assert_subscription_active()

        err = exc_info.value
        assert err.error_code == "subscription_inactive"
        assert err.status_code == 402
        assert err.details["payment_failed_at"] == failed_at.isoformat()
        expected_deadline = failed_at + timedelta(days=7)
        assert err.details["grace_deadline"] == expected_deadline.isoformat()
