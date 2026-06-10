"""Tests for billing enforcement — user limit checking logic."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.entitlements import (
    EntitlementDeniedError,
    EntitlementSet,
    LimitExceededError,
    LimitSet,
)


def _make_state(**overrides) -> BillingState:
    """Build a `BillingState` with safe defaults for the new SHU-774 fields.

    Tests target one or two fields at a time — keep the noise of explicit
    `None`s out of the assertion body.
    """
    base = dict(
        openrouter_key_disabled=False,
        payment_failed_at=None,
        payment_grace_days=0,
        entitlements=EntitlementSet(),
        total_grant_amount=Decimal(0),
        remaining_grant_amount=Decimal(0),
        seat_price_usd=Decimal(0),
        limits=LimitSet(),
        subscription_status=None,
        current_period_start=None,
        current_period_end=None,
        cancel_at_period_end=False,
        canceled_at=None,
        usage_markup_multiplier=None,
        hard_cap=False,
    )
    base.update(overrides)
    return BillingState(**base)
from shu.billing.enforcement import (
    SubscriptionInactiveError,
    HardCapExhaustedError,
    UserLimitStatus,
    assert_document_count_under_limit,
    assert_entitlement,
    assert_kb_count_under_limit,
    assert_subscription_active,
    check_user_limit,
    document_count_batch,
    get_current_billing_state,
    require_entitlement,
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
    async def test_soft_enforcement_preserved_with_accurate_at_limit(self, mock_config, mock_count):
        """`soft` is returned as-is (SHU-784) with `at_limit` reflecting reality.

        Caller in `api/auth.py` reads `enforcement=soft + at_limit` and logs
        a warning while letting registration proceed — the new user lands
        inactive via the SHU_AUTO_ACTIVATE_USERS=false default. Unlike `hard`,
        no row-level lock is acquired (no enforcement → no serialization need).
        """
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
            "user_limit_enforcement": "soft",
        }
        mock_count.return_value = 5
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(5))

        assert result.enforcement == "soft"
        assert result.at_limit is True
        assert result.current_count == 5
        assert result.user_limit == 5

    @pytest.mark.asyncio
    @patch(_P_ACTIVE_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_default_enforcement_is_soft(self, mock_config, mock_count):
        """Missing `user_limit_enforcement` in billing_config defaults to `soft`.

        Matches the `billing_state.user_limit_enforcement` column default;
        adapters surface it as-is rather than substituting another value.
        """
        mock_config.return_value = {
            "stripe_subscription_id": "sub_123",
        }
        mock_count.return_value = 3
        db = AsyncMock()

        result = await check_user_limit(db, _stripe_client_with_seats(5))

        assert result.enforcement == "soft"
        assert result.at_limit is False

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
    async def test_healthy_default_in_cache_fails_closed_via_hard_cap(self, install_stub_cache):
        """Cold-start CP outage on a configured tenant: cache hands out
        `HEALTHY_DEFAULT`, which has `hard_cap=True` and no period anchor
        (Task 10.1 fail-closed posture). The hard-cap branch trips on the
        missing-period-start guard before any usage query runs.

        Distinct from `test_no_cache_does_not_raise` — that's the
        self-hosted bypass (cache singleton missing). This one is the
        outage path on a configured tenant.
        """
        install_stub_cache(HEALTHY_DEFAULT)

        with pytest.raises(HardCapExhaustedError):
            await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_within_grace_does_not_raise(self, install_stub_cache):
        """Payment failed but key still active (within grace) → no raise.

        CP only flips `openrouter_key_disabled=True` after grace ends, so
        a populated `payment_failed_at` with the flag still false is the
        normal in-grace state. `subscription_status="active"` ensures the
        cancel gate doesn't trip.
        """
        install_stub_cache(
            _make_state(
                payment_failed_at=datetime(2026, 1, 1, tzinfo=UTC),
                payment_grace_days=7,
                subscription_status="active",
            )
        )

        await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_disabled_key_raises_with_computed_deadline(self, install_stub_cache):
        """Lockout state → raises with grace_deadline = failed_at + grace_days."""
        failed_at = datetime(2026, 1, 1, tzinfo=UTC)
        install_stub_cache(
            _make_state(
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


# Hard-cap branch — `assert_subscription_active` opens a short-lived
# session via `get_async_session_local()` only when `state.hard_cap=True`,
# then aggregates `LLMUsage` totals via `UsageProviderImpl.get_usage_summary`
# anchored on the wire's `current_period_start`. Patches below mock those two
# touchpoints (DB session + usage provider) so the tests don't need a real DB.

_P_SESSION_LOCAL = "shu.billing.enforcement.get_async_session_local"
_P_USAGE_PROVIDER = "shu.billing.enforcement.UsageProviderImpl"


def _trialing_state(
    *,
    total_grant: Decimal = Decimal("50.00"),
    usage_markup_multiplier: Decimal | None = Decimal("1.0"),
    current_period_start: datetime | None = datetime(2026, 5, 1, tzinfo=UTC),
    subscription_status: str | None = "trialing",
) -> BillingState:
    # Default markup=1.0 keeps existing assertions in raw-dollar terms.
    # The markup-aware test class below passes an explicit multiplier
    # (or None to verify the configured-default fallback path).
    #
    # `current_period_end` is what HardCapExhaustedError now reports as the
    # `period_end` detail (SHU-813 / L2 rename) — pinning it here keeps that
    # error detail deterministic across tests.
    return _make_state(
        total_grant_amount=total_grant,
        remaining_grant_amount=total_grant,
        seat_price_usd=Decimal("20.00"),
        usage_markup_multiplier=usage_markup_multiplier,
        current_period_start=current_period_start,
        current_period_end=datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
        subscription_status=subscription_status,
        hard_cap=True,
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


class TestAssertSubscriptionActiveHardCap:
    """Hard-cap branch of the consolidated assertion."""

    @pytest.mark.asyncio
    async def test_hard_cap_false_with_active_status_does_not_raise(self, install_stub_cache):
        """Non-capped tenant with an active subscription_status passes through
        the cancel gate and short-circuits before the hard-cap branch.
        """
        install_stub_cache(_make_state(subscription_status="active", hard_cap=False))

        # No session needed — non-capped short-circuits before the usage query.
        with patch(_P_SESSION_LOCAL) as session_local:
            await assert_subscription_active()
            session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_tier_non_trialing_hard_cap_enforces_usage(self, install_stub_cache):
        """Free-tier tenant past trial window: `subscription_status` is no
        longer `trialing`, but the tier-intrinsic `hard_cap=True` (CP unifies
        free + trialing into the same wire signal) must still trip the cap.

        The pre-SHU-813 gate keyed on `is_trial` alone would have let this
        tenant rack up unbounded LLM spend. This test pins the new behavior.
        """
        install_stub_cache(
            _make_state(
                subscription_status="active",
                current_period_start=datetime(2026, 5, 1, tzinfo=UTC),
                current_period_end=datetime(2026, 6, 1, tzinfo=UTC),
                total_grant_amount=Decimal("5.00"),
                remaining_grant_amount=Decimal("5.00"),
                usage_markup_multiplier=Decimal("1.0"),
                hard_cap=True,
            )
        )

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("5.00"))),
        ):
            with pytest.raises(HardCapExhaustedError) as exc_info:
                await assert_subscription_active()

        # `period_end` (SHU-813 L2) — must be non-null for a free-tier
        # non-trialing tenant so the frontend can render "budget resets on …"
        # for the cap-exhausted surface. The pre-rename `trial_deadline` was
        # always None outside `trialing`, which gave the free-tier UI nothing
        # to anchor on.
        assert exc_info.value.details["period_end"] == "2026-06-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_canceled_subscription_status_raises_subscription_inactive(self, install_stub_cache):
        """Wire-side `subscription_status == "canceled"` must raise even when
        `openrouter_key_disabled` hasn't flipped yet (CP webhook lag). Source
        of truth lifted to CP in SHU-774.
        """
        install_stub_cache(_make_state(subscription_status="canceled"))

        with pytest.raises(SubscriptionInactiveError) as exc_info:
            await assert_subscription_active()

        assert exc_info.value.error_code == "subscription_inactive"

    @pytest.mark.asyncio
    async def test_below_cap_does_not_raise(self, install_stub_cache):
        install_stub_cache(_trialing_state(total_grant=Decimal("50.00")))

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("12.34"))),
        ):
            await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_at_or_above_cap_raises_hard_cap_exhausted(self, install_stub_cache):
        """Boundary is `total >= grant` — equality blocks too. A tenant
        sitting exactly at the budget shouldn't get one more call through.
        """
        install_stub_cache(_trialing_state(total_grant=Decimal("50.00")))
        period_end = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("50.00"))),
        ):
            with pytest.raises(HardCapExhaustedError) as exc_info:
                await assert_subscription_active()

        err = exc_info.value
        assert err.error_code == "hard_cap_exhausted"
        assert err.status_code == 402
        # `period_end` (SHU-813 L2 rename, was `trial_deadline`) — sourced
        # from `current_period_end` so it's non-null in the free-tier case too.
        assert err.details["period_end"] == period_end.isoformat()
        assert err.details["total_grant_amount"] == "50.00"

    @pytest.mark.asyncio
    async def test_missing_period_start_fails_closed(self, install_stub_cache):
        """Wire-side `current_period_start` is None — usage summary needs a
        period boundary, can't compute. Silent bypass would let unbounded
        spend through, so we treat this as exhausted.
        """
        install_stub_cache(_trialing_state(current_period_start=None))

        with pytest.raises(HardCapExhaustedError):
            await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_payment_failure_takes_precedence_over_hard_cap(self, install_stub_cache):
        """A `past_due` tenant who's also hard-capped must see the
        payment-failure surface (the binding gate), not the cap one.
        Hard-cap branch must not even open a session.
        """
        install_stub_cache(
            _make_state(
                openrouter_key_disabled=True,
                payment_failed_at=datetime(2026, 1, 1, tzinfo=UTC),
                payment_grace_days=7,
                subscription_status="trialing",
                current_period_end=datetime(2026, 5, 30, tzinfo=UTC),
                total_grant_amount=Decimal("50.00"),
                remaining_grant_amount=Decimal("50.00"),
                seat_price_usd=Decimal("20.00"),
                hard_cap=True,
            )
        )

        with patch(_P_SESSION_LOCAL) as session_local:
            with pytest.raises(SubscriptionInactiveError):
                await assert_subscription_active()
            session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_does_not_open_session(self, install_stub_cache):
        """`HEALTHY_DEFAULT.hard_cap=True` (cold-start fail-closed posture)
        would route self-hosted dev tenants into the hard-cap branch
        without the explicit cache=None bypass at the top of the function.
        Pinning that bypass here keeps dev usable.
        """
        # install_stub_cache resets the singleton on setup; not calling
        # `_install` leaves _cache=None — the self-hosted shape.
        with patch(_P_SESSION_LOCAL) as session_local:
            await assert_subscription_active()
            session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_markup_pushes_below_cap_usage_over_threshold(self, install_stub_cache):
        """Raw cost $40 on a $50 grant would pass without markup. With
        markup=1.3 the billed cost is $52 — over cap, must raise.
        """
        install_stub_cache(
            _trialing_state(
                total_grant=Decimal("50.00"),
                usage_markup_multiplier=Decimal("1.3"),
            )
        )

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("40.00"))),
        ):
            with pytest.raises(HardCapExhaustedError):
                await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_markup_keeps_low_usage_under_threshold(self, install_stub_cache):
        """Raw cost $10 with markup=1.3 → billed $13, well under $50 grant.
        Mirror of the above so the threshold isn't accidentally one-sided.
        """
        install_stub_cache(
            _trialing_state(
                total_grant=Decimal("50.00"),
                usage_markup_multiplier=Decimal("1.3"),
            )
        )

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("10.00"))),
        ):
            await assert_subscription_active()

    @pytest.mark.asyncio
    async def test_unset_markup_falls_back_to_configured_default(
        self, install_stub_cache, monkeypatch
    ):
        """Stripe blip during the last refresh → markup field left as None.
        The trial-cap math must still apply *a* multiplier (the configured
        default) so the cap isn't silently disabled mid-outage.
        """
        from shu.billing import markup as markup_mod

        # Pin the default to a known value; raw cost $30 * 1.5 = $45 < $50,
        # but $40 * 1.5 = $60 > $50 — the threshold case below would have
        # passed without the default applied.
        settings = markup_mod.get_billing_settings()
        monkeypatch.setattr(settings, "usage_markup_multiplier_default", Decimal("1.5"))

        install_stub_cache(
            _trialing_state(
                total_grant=Decimal("50.00"),
                usage_markup_multiplier=None,
            )
        )

        with (
            patch(_P_SESSION_LOCAL, _session_local_factory()),
            patch(_P_USAGE_PROVIDER, _usage_provider_returning(Decimal("40.00"))),
        ):
            with pytest.raises(HardCapExhaustedError):
                await assert_subscription_active()


# Entitlement enforcement — `assert_entitlement(key)` reads the cached
# state and raises `EntitlementDeniedError` if the key is not True.
# `require_entitlement(key)` wraps it in a FastAPI dep factory.


def _state_with_entitlements(**overrides) -> BillingState:
    """Healthy non-trial state with custom entitlements."""
    return _make_state(entitlements=EntitlementSet(**overrides))


class TestAssertEntitlement:
    """Behavior of `assert_entitlement(key)`."""

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_is_no_op(self, install_stub_cache):
        """Cache singleton missing (self-hosted/dev) → no enforcement.
        Without this bypass, every entitlement-gated route would 403 in
        dev environments where CP isn't configured.
        """
        # Fixture resets the singleton; not installing leaves _cache=None.
        await assert_entitlement("plugins")

    @pytest.mark.asyncio
    async def test_entitlement_true_does_not_raise(self, install_stub_cache):
        install_stub_cache(_state_with_entitlements(plugins=True))
        await assert_entitlement("plugins")

    @pytest.mark.asyncio
    async def test_entitlement_false_raises_with_typed_details(self, install_stub_cache):
        install_stub_cache(_state_with_entitlements(plugins=False))

        with pytest.raises(EntitlementDeniedError) as exc_info:
            await assert_entitlement("plugins")

        err = exc_info.value
        assert err.error_code == "entitlement_denied"
        assert err.status_code == 403
        assert err.details == {"entitlement": "plugins"}
        assert err.key == "plugins"

    @pytest.mark.asyncio
    async def test_unknown_key_raises(self, install_stub_cache):
        """Defensive: a route declaration that passes a key not on the
        `EntitlementSet` (typo, removed feature) should fail closed.
        `getattr(state.entitlements, "<typo>", False)` returns False.
        """
        install_stub_cache(_state_with_entitlements(plugins=True))

        with pytest.raises(EntitlementDeniedError) as exc_info:
            await assert_entitlement("not_a_real_entitlement")

        assert exc_info.value.key == "not_a_real_entitlement"

    @pytest.mark.asyncio
    async def test_healthy_default_routes_to_chat_only(self, install_stub_cache):
        """Cold-start outage with cache configured: HEALTHY_DEFAULT has
        chat=True, everything else False. `chat` passes, others 403.
        Pins the fail-closed posture from Task 10.1 against the gating layer.
        """
        install_stub_cache(HEALTHY_DEFAULT)

        await assert_entitlement("chat")  # baseline open

        with pytest.raises(EntitlementDeniedError):
            await assert_entitlement("plugins")


class TestRequireEntitlement:
    """Behavior of the `require_entitlement(key)` FastAPI dep factory."""

    @pytest.mark.asyncio
    async def test_dep_callable_delegates_to_assert_entitlement(self, install_stub_cache):
        """The returned dep is a thin wrapper — verify it raises when the
        underlying `assert_entitlement` would, and no-ops when it wouldn't.
        Two cases covers the wrapper; deeper behavior is pinned by the
        `TestAssertEntitlement` cases above.
        """
        install_stub_cache(_state_with_entitlements(plugins=False))
        dep = require_entitlement("plugins")
        with pytest.raises(EntitlementDeniedError):
            await dep()

        install_stub_cache(_state_with_entitlements(plugins=True))
        dep = require_entitlement("plugins")
        await dep()  # no raise


# Resource limit enforcement — `assert_kb_count_under_limit` and
# `assert_document_count_under_limit` each read the cached BillingState,
# acquire a per-tenant FOR UPDATE lock, and raise `LimitExceededError`
# when the live count is at or over the cap. The two helpers differ only
# in which `LimitSet` field they read and which model they count, so the
# behavior is exercised once via parametrization rather than duplicated.


def _state_with_limits(**overrides) -> BillingState:
    """Healthy non-trial state with custom limit caps."""
    return _make_state(limits=LimitSet(**overrides))


def _db_returning_count(count: int) -> AsyncMock:
    """AsyncSession mock where `await db.execute(...).scalar()` yields `count`."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = count
    db.execute.return_value = result
    return db


_LIMIT_HELPER_PARAMS = [
    pytest.param(
        assert_kb_count_under_limit,
        "kb_count_limit",
        "kb_count",
        id="kb_count",
    ),
    pytest.param(
        assert_document_count_under_limit,
        "document_count_limit",
        "document_count",
        id="document_count",
    ),
]


@pytest.mark.parametrize(("helper", "cap_field", "expected_limit_key"), _LIMIT_HELPER_PARAMS)
class TestAssertCountUnderLimit:
    """Behavior of `assert_kb_count_under_limit` and `assert_document_count_under_limit`."""

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_is_no_op(
        self, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        """Cache singleton missing → no enforcement and no DB access.
        Mirrors `assert_entitlement`'s bypass so dev environments aren't
        gated on a CP that isn't configured. The fixture's autouse reset
        leaves the slot as None when `_install` is not called.
        """
        db = AsyncMock()
        await helper(db)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_under_cap_does_not_raise(
        self, mock_lock, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        install_stub_cache(_state_with_limits(**{cap_field: 5}))
        db = _db_returning_count(3)

        await helper(db)

        mock_lock.assert_awaited_once_with(db)

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_at_cap_raises_with_typed_details(
        self, _mock_lock, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        install_stub_cache(_state_with_limits(**{cap_field: 5}))
        db = _db_returning_count(5)

        with pytest.raises(LimitExceededError) as exc_info:
            await helper(db)

        err = exc_info.value
        assert err.error_code == "limit_exceeded"
        assert err.status_code == 403
        assert err.details == {"limit": expected_limit_key, "cap": 5, "current": 5}
        assert err.limit == expected_limit_key
        assert err.cap == 5
        assert err.current == 5

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_over_cap_raises(
        self, _mock_lock, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        install_stub_cache(_state_with_limits(**{cap_field: 5}))
        db = _db_returning_count(7)

        with pytest.raises(LimitExceededError) as exc_info:
            await helper(db)

        assert exc_info.value.cap == 5
        assert exc_info.value.current == 7

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_zero_cap_blocks_even_at_zero_count(
        self, _mock_lock, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        """HEALTHY_DEFAULT ships limits=(0,0); the strict-zeros decision
        (SHU-776) means even a brand-new tenant at count=0 is blocked
        until CP supplies real caps. Pins the fail-closed posture against
        the count-vs-cap branch.
        """
        install_stub_cache(_state_with_limits(**{cap_field: 0}))
        db = _db_returning_count(0)

        with pytest.raises(LimitExceededError) as exc_info:
            await helper(db)

        assert exc_info.value.cap == 0
        assert exc_info.value.current == 0

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_acquires_lock_before_counting(
        self, mock_lock, install_stub_cache, helper, cap_field, expected_limit_key
    ):
        """FOR UPDATE must be held before the count query, otherwise two
        concurrent creates at current=cap-1 could both pass the check.
        """
        install_stub_cache(_state_with_limits(**{cap_field: 5}))

        call_order: list[str] = []

        async def _record_lock(_db):
            call_order.append("lock")

        mock_lock.side_effect = _record_lock

        db = AsyncMock()
        result = MagicMock()
        result.scalar.return_value = 3

        async def _record_execute(*_args, **_kwargs):
            call_order.append("execute")
            return result

        db.execute.side_effect = _record_execute

        await helper(db)

        assert call_order == ["lock", "execute"]


class TestDocumentCountBatch:
    """`document_count_batch()` counts the DB once per batch (SHU-776 M1) but
    still tracks remaining capacity in memory, so a batch can't push its own
    inserts past the cap.
    """

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_counts_once_then_tracks_in_memory(self, mock_lock, install_stub_cache):
        install_stub_cache(_state_with_limits(document_count_limit=5))
        db = _db_returning_count(0)  # plenty of headroom (remaining=5)

        async with document_count_batch():
            await assert_document_count_under_limit(db)
            await assert_document_count_under_limit(db)
            await assert_document_count_under_limit(db)

        # The DB was counted (and locked) exactly once for the whole batch.
        assert db.execute.await_count == 1
        mock_lock.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_cap_minus_one_allows_one_then_blocks(self, _mock_lock, install_stub_cache):
        """current=cap-1 with two new docs in one batch: the first lands, the
        second is rejected — the batch can't overshoot via its own inserts.
        """
        install_stub_cache(_state_with_limits(document_count_limit=5))
        db = _db_returning_count(4)  # remaining = 1

        async with document_count_batch():
            await assert_document_count_under_limit(db)  # consumes the last slot
            with pytest.raises(LimitExceededError):
                await assert_document_count_under_limit(db)

        # Still only one DB count — the second rejection is in-memory.
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_per_call_outside_batch(self, _mock_lock, install_stub_cache):
        install_stub_cache(_state_with_limits(document_count_limit=5))
        db = _db_returning_count(3)

        await assert_document_count_under_limit(db)
        await assert_document_count_under_limit(db)

        # No batch scope (e.g. a direct upload) → each call re-checks: hard cap.
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_at_cap_blocks_every_item_in_batch(self, _mock_lock, install_stub_cache):
        install_stub_cache(_state_with_limits(document_count_limit=5))
        db = _db_returning_count(5)  # remaining = 0

        async with document_count_batch():
            with pytest.raises(LimitExceededError) as first:
                await assert_document_count_under_limit(db)
            # remaining stays 0, so the next item is rejected in-memory.
            with pytest.raises(LimitExceededError):
                await assert_document_count_under_limit(db)

        assert first.value.details == {"limit": "document_count", "cap": 5, "current": 5}
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_bypass_when_cache_missing(self, _mock_lock, install_stub_cache):
        # No cache installed → self-hosted bypass; the batch never touches the DB.
        db = _db_returning_count(0)
        async with document_count_batch():
            await assert_document_count_under_limit(db)
            await assert_document_count_under_limit(db)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @patch(_P_STATE_SERVICE, new_callable=AsyncMock)
    async def test_batch_scope_resets_after_exit(self, _mock_lock, install_stub_cache):
        install_stub_cache(_state_with_limits(document_count_limit=5))
        db = _db_returning_count(0)

        async with document_count_batch():
            await assert_document_count_under_limit(db)
        # Outside the scope again → next batch re-seeds from a fresh count.
        async with document_count_batch():
            await assert_document_count_under_limit(db)

        assert db.execute.await_count == 2
