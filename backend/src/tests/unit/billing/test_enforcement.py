"""Tests for billing enforcement — user limit checking logic."""

from unittest.mock import AsyncMock, patch

import pytest

from shu.billing.enforcement import UserLimitStatus, check_user_limit

_P_BILLING_CONFIG = "shu.billing.enforcement.get_billing_config"
_P_ACTIVE_USER_COUNT = "shu.billing.enforcement.get_active_user_count"
_P_STATE_SERVICE = "shu.billing.state_service.BillingStateService.get_for_update"


def _stripe_client_with_seats(seats: int) -> AsyncMock:
    client = AsyncMock()
    client.get_subscription_seat_state = AsyncMock(return_value=(seats, seats, None))
    return client


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
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_returns_none_enforcement_when_quantity_zero(self, mock_config, mock_count):
        """quantity=0 → treat as unlimited, active-count lookup skipped."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
        }
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(0))

        assert result.enforcement == "none"
        mock_count.assert_not_called()

    @pytest.mark.asyncio
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_soft_enforcement_is_treated_as_none(self, mock_config, mock_count):
        """`soft` is a legal legacy value but always normalises to `none`.

        B1 disables `soft` until it has a real behavior separate from `hard`
        — legacy rows with soft-set values must not block or warn.
        """
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "soft",
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(5))

        assert result.enforcement == "none"
        assert result.at_limit is True
        assert result.current_count == 5

    @pytest.mark.asyncio
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_default_enforcement_is_normalised_to_none(self, mock_config, mock_count):
        """Missing `user_limit_enforcement` defaults to `soft` → normalised to `none`."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
        }
        mock_count.return_value = 3
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(5))

        assert result.enforcement == "none"

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_at_limit_computed_against_active_count_not_total(
        self, mock_config, mock_count, _mock_lock
    ):
        """Pending (is_active=False) users must not consume seats.

        get_active_user_count is mocked to return only active rows; the
        verification is that check_user_limit calls this helper rather than
        a total-user helper.
        """
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 2  # two active; two pending users are invisible
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(2))

        assert result.at_limit is True
        assert result.current_count == 2
        mock_count.assert_awaited_once_with(db)

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_adding_pending_user_does_not_flip_at_limit(
        self, mock_config, mock_count, _mock_lock
    ):
        """Adding an inactive user leaves at_limit unchanged."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 1  # one active; any number of pending
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(2))

        assert result.at_limit is False

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_hard_enforcement_at_limit(self, mock_config, mock_count, _mock_lock):
        """hard + active-count >= quantity → at_limit=True, enforcement preserved."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(3))

        assert result.enforcement == "hard"
        assert result.at_limit is True
        assert result.user_limit == 3
        assert result.current_count == 5

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_hard_enforcement_below_limit(self, mock_config, mock_count, _mock_lock):
        """hard + active-count < quantity → at_limit=False."""
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "hard",
        }
        mock_count.return_value = 3
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(5))

        assert result.enforcement == "hard"
        assert result.at_limit is False


class TestUserLimitStatus:
    """Tests for the UserLimitStatus dataclass."""

    def test_is_frozen(self):
        """UserLimitStatus should be immutable."""
        s = UserLimitStatus(enforcement="soft", at_limit=True, current_count=5, user_limit=5)
        with pytest.raises(AttributeError):
            s.at_limit = False  # type: ignore[misc]
