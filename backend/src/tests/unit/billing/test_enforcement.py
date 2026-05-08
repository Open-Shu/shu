"""Tests for billing enforcement — user limit checking logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.entitlements import EntitlementSet
from shu.billing.enforcement import (
    SubscriptionInactiveError,
    TrialCapExhaustedError,
    UserLimitStatus,
    assert_subscription_active,
    check_user_limit,
    get_current_billing_state,
)

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
    async def test_healthy_default_in_cache_fails_closed_via_trial_cap(self, install_stub_cache):
        """Cold-start CP outage on a configured tenant: cache hands out
        `HEALTHY_DEFAULT`, which has `is_trial=True` and
        `total_grant_amount=0` (Task 10.1 fail-closed posture). The
        trial-cap branch must trip the moment we enter it, even with
        zero recorded usage, because `0 >= 0` is true.

        Distinct from `test_no_cache_does_not_raise` — that's the
        self-hosted bypass (cache singleton missing). This one is the
        outage path on a configured tenant.
        """
        install_stub_cache(HEALTHY_DEFAULT)

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_billing_row())),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("0"))),
        ):
            with pytest.raises(TrialCapExhaustedError):
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
                entitlements=EntitlementSet(),
                is_trial=False,
                trial_deadline=None,
                total_grant_amount=Decimal(0),
                remaining_grant_amount=Decimal(0),
                seat_price_usd=Decimal(0),
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
                entitlements=EntitlementSet(),
                is_trial=False,
                trial_deadline=None,
                total_grant_amount=Decimal(0),
                remaining_grant_amount=Decimal(0),
                seat_price_usd=Decimal(0),
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


# Trial-cap branch — `assert_subscription_active` opens a short-lived
# session via `get_async_session_local()` only when `state.is_trial=True`,
# queries `BillingStateService.get` for the period start, then aggregates
# `LLMUsage` totals via `UsageProviderImpl.get_usage_summary`. Patches
# below mock those three touchpoints so the tests don't need a real DB.

_P_SESSION_LOCAL = "shu.billing.enforcement.get_async_session_local"
_P_BILLING_STATE_GET = "shu.billing.enforcement.BillingStateService.get"
_P_USAGE_PROVIDER = "shu.billing.enforcement.UsageProviderImpl"


def _trialing_state(*, total_grant: Decimal = Decimal("50.00")) -> BillingState:
    return BillingState(
        openrouter_key_disabled=False,
        payment_failed_at=None,
        payment_grace_days=0,
        entitlements=EntitlementSet(),
        is_trial=True,
        trial_deadline=datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
        total_grant_amount=total_grant,
        remaining_grant_amount=total_grant,
        seat_price_usd=Decimal("20.00"),
    )


def _session_local_factory() -> MagicMock:
    """Stub `get_async_session_local()` → factory returning a context-manager.

    Three layers, mirroring the production call chain:
      get_async_session_local()   → sessionmaker (sync call)
      sessionmaker()              → AsyncSession context-manager
      `async with session as db`  → db
    """

    class _CtxSession:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_):
            return None

    sessionmaker = MagicMock(return_value=_CtxSession())
    factory = MagicMock(return_value=sessionmaker)
    return factory


def _usage_provider_returning(total_cost: Decimal) -> MagicMock:
    """Stub `UsageProviderImpl(db)` → instance.get_usage_summary -> summary.

    UsageProviderImpl is the class — instantiation is sync (MagicMock).
    `get_usage_summary` is async (AsyncMock).
    """
    summary = MagicMock()
    summary.total_cost_usd = total_cost
    instance = MagicMock()
    instance.get_usage_summary = AsyncMock(return_value=summary)
    cls = MagicMock(return_value=instance)
    return cls


def _billing_row(*, period_start: datetime | None = datetime(2026, 5, 1, tzinfo=UTC)):
    row = MagicMock()
    row.current_period_start = period_start
    return row


class TestAssertSubscriptionActiveTrialCap:
    """Trial-cap branch of the consolidated assertion."""

    @pytest.mark.asyncio
    async def test_is_trial_false_does_not_open_session(self, install_stub_cache):
        """The DB query is gated on is_trial=True. Non-trial tenants must
        not pay the session-open cost on every LLM call.
        """
        install_stub_cache(HEALTHY_DEFAULT.__class__(
            openrouter_key_disabled=False,
            payment_failed_at=None,
            payment_grace_days=0,
            entitlements=EntitlementSet(),
            is_trial=False,
            trial_deadline=None,
            total_grant_amount=Decimal(0),
            remaining_grant_amount=Decimal(0),
            seat_price_usd=Decimal(0),
        ))

        with patch(_P_SESSION_LOCAL) as session_local:
            await assert_subscription_active()
            session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_cap_does_not_raise(self, install_stub_cache):
        install_stub_cache(_trialing_state(total_grant=Decimal("50.00")))

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_billing_row())),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("12.34"))),
        ):
            await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_at_or_above_cap_raises_trial_cap_exhausted(self, install_stub_cache):
        """Boundary is `total >= grant` — equality blocks too. A tenant
        sitting exactly at the budget shouldn't get one more call through.
        """
        install_stub_cache(_trialing_state(total_grant=Decimal("50.00")))
        trial_deadline = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_billing_row())),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("50.00"))),
        ):
            with pytest.raises(TrialCapExhaustedError) as exc_info:
                await assert_subscription_active()

        err = exc_info.value
        assert err.error_code == "trial_usage_exhausted"
        assert err.status_code == 402
        assert err.details["trial_deadline"] == trial_deadline.isoformat()
        assert err.details["total_grant_amount"] == "50.00"

    @pytest.mark.asyncio
    async def test_missing_billing_row_fails_closed(self, install_stub_cache):
        """Data anomaly: trialing state but no `billing_state` row. Silent
        bypass would let unbounded trial spend through, so we treat this
        as exhausted.
        """
        install_stub_cache(_trialing_state())

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=None)),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("0"))),
        ):
            with pytest.raises(TrialCapExhaustedError):
                await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_missing_period_start_fails_closed(self, install_stub_cache):
        """Same fail-closed logic when the row exists but `current_period_start`
        is None — usage summary needs a period boundary, can't compute.
        """
        install_stub_cache(_trialing_state())

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_billing_row(period_start=None))),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("0"))),
        ):
            with pytest.raises(TrialCapExhaustedError):
                await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_payment_failure_takes_precedence_over_trial_cap(self, install_stub_cache):
        """A `past_due` tenant who's also trialing must see the
        payment-failure surface (the binding gate), not the trial one.
        Trial-cap branch must not even open a session.
        """
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=True,
                payment_failed_at=datetime(2026, 1, 1, tzinfo=UTC),
                payment_grace_days=7,
                entitlements=EntitlementSet(),
                is_trial=True,
                trial_deadline=datetime(2026, 5, 30, tzinfo=UTC),
                total_grant_amount=Decimal("50.00"),
                remaining_grant_amount=Decimal("50.00"),
                seat_price_usd=Decimal("20.00"),
            )
        )

        with patch(_P_SESSION_LOCAL) as session_local:
            with pytest.raises(SubscriptionInactiveError):
                await assert_subscription_active()
            session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_does_not_open_session(self, install_stub_cache):
        """`HEALTHY_DEFAULT.is_trial=True` (cold-start fail-closed posture)
        would route self-hosted dev tenants into the trial-cap branch
        without the explicit cache=None bypass at the top of the function.
        Pinning that bypass here keeps dev usable.
        """
        # install_stub_cache resets the singleton on setup; not calling
        # `_install` leaves _cache=None — the self-hosted shape.
        with patch(_P_SESSION_LOCAL) as session_local:
            await assert_subscription_active()
            session_local.assert_not_called()
