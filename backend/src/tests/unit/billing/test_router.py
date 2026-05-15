"""Unit tests for the billing router.

Covers the payment-status block on `/billing/subscription` introduced for
SHU-703 (banner-facing fields visible to all authenticated users) plus the
unchanged admin-gating of the Stripe metadata block.

These call the endpoint function directly with mocked deps, per CLAUDE.md
guidance for API unit tests — no FastAPI app, no httpx transport.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState, CpAuthFailed, CpNoActiveTrial
from shu.billing.entitlements import EntitlementSet
from shu.billing.router import cancel_trial, get_subscription_status, upgrade_now

_P_BILLING_CONFIG = "shu.billing.router.get_billing_config"
_P_USER_COUNT = "shu.billing.router.get_active_user_count"
_P_BILLING_STATE_GET = "shu.billing.router.BillingStateService.get"


@pytest.fixture(autouse=True)
def _stub_billing_state_service():
    """Patch BillingStateService.get to a no-op by default.

    The local-fallback path in `_resolve_remaining_grant_amount` calls it
    when the cached state has `remaining_grant_amount=None` (CP returns
    None during trial). Tests that pass an `AsyncMock` session can't
    naturally satisfy the staticmethod's `await db.execute(...)` chain;
    patching here keeps existing tests focused on whichever payload field
    they actually assert. Tests that exercise the local-fallback math
    directly override this patch.
    """
    with patch(_P_BILLING_STATE_GET, AsyncMock(return_value=None)):
        yield


def _mock_user(*, is_admin: bool):
    user = MagicMock()
    user.can_manage_users.return_value = is_admin
    return user


def _mock_settings(*, is_configured: bool = True):
    settings = MagicMock()
    settings.is_configured = is_configured
    return settings


def _decode(response) -> dict:
    return json.loads(response.body.decode())["data"]


# Baseline billing config — no subscription, so admin block is mostly null.
# Tests that assert specifically on the admin block override this.
_EMPTY_CONFIG: dict = {}


class TestSubscriptionPaymentStatus:
    """Payment-status fields exposed to all authenticated users."""

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_healthy_state_returns_default_payment_fields(
        self, mock_config, mock_count, install_stub_cache
    ):
        """Cache=HEALTHY_DEFAULT → payment_failed_at null, service_paused false."""
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
        install_stub_cache(HEALTHY_DEFAULT)

        response = await get_subscription_status(db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings())
        body = _decode(response)

        assert body["payment_failed_at"] is None
        assert body["payment_grace_days"] == 0
        assert body["grace_deadline"] is None
        assert body["service_paused"] is False

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_paused_state_with_failed_at_populates_grace_deadline(
        self, mock_config, mock_count, install_stub_cache
    ):
        """key_disabled=True + payment_failed_at set → service_paused, deadline computed."""
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
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

        response = await get_subscription_status(db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings())
        body = _decode(response)

        assert body["service_paused"] is True
        assert body["payment_failed_at"] == failed_at.isoformat()
        assert body["payment_grace_days"] == 7
        assert body["grace_deadline"] == (failed_at + timedelta(days=7)).isoformat()

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_self_hosted_cache_none_returns_default_healthy(
        self, mock_config, mock_count, install_stub_cache
    ):
        """cache=None (self-hosted) → all four fields default-healthy."""
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
        # Fixture resets cache on setup; we don't install — endpoint sees None.

        response = await get_subscription_status(db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings())
        body = _decode(response)

        assert body["payment_failed_at"] is None
        assert body["payment_grace_days"] == 0
        assert body["grace_deadline"] is None
        assert body["service_paused"] is False


# Admin-block-gating tests.
#
# The endpoint's docstring claims "Non-admin users receive quota fields only.
# Admin users additionally receive sensitive Stripe identifiers and billing
# period details." That contract was previously asserted only by inspection;
# these tests pin the fields that may NOT leak to non-admins.
_ADMIN_ONLY_KEYS = (
    "stripe_customer_id",
    "stripe_subscription_id",
    "subscription_status",
    "current_period_start",
    "current_period_end",
    "cancel_at_period_end",
)

_FULL_BILLING_CONFIG = {
    "stripe_customer_id": "cus_admin_only",
    "stripe_subscription_id": "sub_admin_only",
    "subscription_status": "active",
    "current_period_start": "2026-01-01T00:00:00+00:00",
    "current_period_end": "2026-02-01T00:00:00+00:00",
    "cancel_at_period_end": False,
    "quantity": 5,
    "user_limit_enforcement": "soft",
}


class TestSubscriptionAdminBlock:
    """Stripe identifiers and billing-period fields must remain admin-only."""

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_admin_user_receives_stripe_block(
        self, mock_config, mock_count, install_stub_cache
    ):
        mock_config.return_value = _FULL_BILLING_CONFIG
        mock_count.return_value = 2
        install_stub_cache(HEALTHY_DEFAULT)

        # is_configured=False skips the live Stripe seat-state branch — these
        # tests pin admin-block visibility from billing_config, not Stripe.
        response = await get_subscription_status(db=AsyncMock(), user=_mock_user(is_admin=True), settings=_mock_settings(is_configured=False))
        body = _decode(response)

        for key in _ADMIN_ONLY_KEYS:
            assert key in body, f"admin block missing field: {key}"
        assert body["stripe_customer_id"] == "cus_admin_only"
        assert body["stripe_subscription_id"] == "sub_admin_only"
        assert body["subscription_status"] == "active"

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_non_admin_user_does_not_receive_stripe_block(
        self, mock_config, mock_count, install_stub_cache
    ):
        # Same backing config as the admin test — only the user-role
        # branch should change what comes out the other end.
        mock_config.return_value = _FULL_BILLING_CONFIG
        mock_count.return_value = 2
        install_stub_cache(HEALTHY_DEFAULT)

        response = await get_subscription_status(db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings(is_configured=False))
        body = _decode(response)

        leaked = [key for key in _ADMIN_ONLY_KEYS if key in body]
        assert leaked == [], f"admin-only fields leaked to non-admin: {leaked}"


# Trial / entitlement payload — visible to all authenticated users so the
# frontend banner and entitlement-driven route gating work without admin
# privileges. These fields ride on the same endpoint payload (no new endpoint).
_TRIAL_PAYLOAD_KEYS = (
    "is_trial",
    "trial_deadline",
    "total_grant_amount",
    "remaining_grant_amount",
    "seat_price_usd",
    "entitlements",
)


class TestSubscriptionTrialAndEntitlements:
    """Trial / grant / entitlement fields propagate to all authenticated users."""

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_payload_includes_all_new_trial_and_entitlement_fields(
        self, mock_config, mock_count, install_stub_cache
    ):
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
        install_stub_cache(
            BillingState(
                openrouter_key_disabled=False,
                payment_failed_at=None,
                payment_grace_days=0,
                entitlements=EntitlementSet(plugins=True, experiences=True),
                is_trial=True,
                trial_deadline=datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC),
                total_grant_amount=Decimal("50.00"),
                remaining_grant_amount=Decimal("12.34"),
                seat_price_usd=Decimal("20.00"),
            )
        )

        response = await get_subscription_status(
            db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings()
        )
        body = _decode(response)

        for key in _TRIAL_PAYLOAD_KEYS:
            assert key in body, f"trial/entitlement payload missing field: {key}"
        assert body["is_trial"] is True
        assert body["trial_deadline"] == "2026-05-30T12:00:00+00:00"
        # Decimals stringified to dodge JSON-number precision loss.
        assert body["total_grant_amount"] == "50.00"
        assert body["remaining_grant_amount"] == "12.34"
        assert body["seat_price_usd"] == "20.00"
        assert body["entitlements"] == {
            "chat": True,
            "plugins": True,
            "experiences": True,
            "provider_management": False,
            "model_config_management": False,
            "mcp_servers": False,
        }

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_trial_deadline_serializes_to_null_when_absent(
        self, mock_config, mock_count, install_stub_cache
    ):
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
        # HEALTHY_DEFAULT has trial_deadline=None — covers the null path; the
        # ISO 8601 path is asserted above.
        install_stub_cache(HEALTHY_DEFAULT)

        response = await get_subscription_status(
            db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings()
        )
        body = _decode(response)

        assert body["trial_deadline"] is None

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_non_admin_user_receives_trial_and_entitlement_fields(
        self, mock_config, mock_count, install_stub_cache
    ):
        """Trial / entitlement fields are NOT admin-gated — the banner needs
        them on every authenticated session.
        """
        mock_config.return_value = _FULL_BILLING_CONFIG
        mock_count.return_value = 2
        install_stub_cache(HEALTHY_DEFAULT)

        response = await get_subscription_status(
            db=AsyncMock(),
            user=_mock_user(is_admin=False),
            settings=_mock_settings(is_configured=False),
        )
        body = _decode(response)

        for key in _TRIAL_PAYLOAD_KEYS:
            assert key in body, f"non-admin missing trial/entitlement field: {key}"

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    async def test_entitlements_dump_carries_all_six_keys(
        self, mock_config, mock_count, install_stub_cache
    ):
        """The wire shape must include every entitlement key, even those at
        their default value, so the frontend can rely on key presence rather
        than `.get(key, default)` everywhere.
        """
        mock_config.return_value = _EMPTY_CONFIG
        mock_count.return_value = 0
        install_stub_cache(HEALTHY_DEFAULT)

        response = await get_subscription_status(
            db=AsyncMock(), user=_mock_user(is_admin=False), settings=_mock_settings()
        )
        body = _decode(response)

        assert set(body["entitlements"].keys()) == {
            "chat",
            "plugins",
            "experiences",
            "provider_management",
            "model_config_management",
            "mcp_servers",
        }


# Trial-action endpoints — upgrade-now and cancel-trial share the same
# error-handling and cache-invalidation shape, so all cases parametrize
# over both. Admin gating (`Depends(require_admin)`) is framework-enforced
# and tested at the dep's definition site; intentionally not re-asserted
# here per "test our code, not the framework." Cancel-trial relies on
# the frontend's typed-confirmation prompt for accidental-click protection;
# the backend has no server-side token check (admin role is the binding gate).


def _mock_admin_user() -> MagicMock:
    user = MagicMock()
    user.id = "admin-user-1"
    user.can_manage_users.return_value = True
    return user


def _stub_cp_cache(*, cp_call_outcome: Exception | None = None) -> tuple[MagicMock, MagicMock]:
    """Build a stub cache + CpClient pair, mirroring the two singletons
    populated together by `initialize_billing_state_cache`.

    Returns `(cache, cp_client)`. Tests patch both
    `shu.billing.router.get_billing_state_cache` and
    `shu.billing.router.get_cp_client` to return these — splitting the
    singletons keeps the test setup honest about which one the production
    path actually reads from.

    `cp_call_outcome` is either None (CP call succeeds) or an exception
    instance the CP method should raise. Both methods share the same
    outcome — tests target one method at a time.
    """
    cache = MagicMock()
    cache.invalidate = AsyncMock()
    cp_client = MagicMock()
    if cp_call_outcome is None:
        cp_client.post_upgrade_now = AsyncMock(return_value=None)
        cp_client.post_cancel_subscription = AsyncMock(return_value=None)
    else:
        cp_client.post_upgrade_now = AsyncMock(side_effect=cp_call_outcome)
        cp_client.post_cancel_subscription = AsyncMock(side_effect=cp_call_outcome)
    return cache, cp_client


async def _call_endpoint(endpoint_name: str):
    """Invoke the upgrade-now or cancel-trial endpoint directly.

    `cancel_trial` takes a `db` dependency for the inline
    `subscription_status="canceled"` write. We pass a MagicMock and patch
    `BillingStateService.update` at call sites that exercise the happy
    path so the write becomes a no-op; error paths never reach the write.
    """
    if endpoint_name == "upgrade_now":
        return await upgrade_now(user=_mock_admin_user())
    return await cancel_trial(user=_mock_admin_user(), db=MagicMock())


_ENDPOINTS = ["upgrade_now", "cancel_trial"]


class TestTrialActionEndpoints:
    """Shared behaviors of `/upgrade-now` and `/cancel-trial`."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", _ENDPOINTS)
    async def test_happy_path_returns_200_and_invalidates_cache(self, endpoint: str):
        cache, cp_client = _stub_cp_cache()
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
            # cancel_trial's inline `subscription_status` write lands here on
            # success; stub it so the MagicMock db doesn't try to run real SQL.
            patch("shu.billing.router.BillingStateService.update", AsyncMock()),
        ):
            response = await _call_endpoint(endpoint)

        assert response.status_code == 200
        assert _decode(response) == {"ok": True}
        cache.invalidate.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", _ENDPOINTS)
    async def test_no_cache_returns_503(self, endpoint: str):
        """Self-hosted / dev — no CP cache (and no CP client) means no trial
        to act on. Either singleton missing trips the 503 guard.
        """
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=None),
            patch("shu.billing.router.get_cp_client", return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _call_endpoint(endpoint)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", _ENDPOINTS)
    async def test_cp_no_active_trial_returns_409(self, endpoint: str):
        cache, cp_client = _stub_cp_cache(cp_call_outcome=CpNoActiveTrial(status_code=409))
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _call_endpoint(endpoint)
        assert exc_info.value.status_code == 409
        # Cache not invalidated on a failed action — next read should still
        # serve the existing cached state.
        cache.invalidate.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", _ENDPOINTS)
    async def test_cp_auth_failure_returns_502(self, endpoint: str):
        """Generic CpClientError (including `CpAuthFailed`) collapses to
        502 — the tenant treats it as upstream dependency failure.
        """
        cache, cp_client = _stub_cp_cache(cp_call_outcome=CpAuthFailed("bad sig"))
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await _call_endpoint(endpoint)
        assert exc_info.value.status_code == 502
        cache.invalidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_trial_writes_subscription_status_canceled_inline(self):
        """The cancel-trial endpoint must write `subscription_status="canceled"`
        to local billing_state on success. Without this, `assert_subscription_active`
        falls through the cancel gate during the window between Stripe flipping
        the sub to `canceled` and CP's webhook landing.
        """
        cache, cp_client = _stub_cp_cache()
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
            patch("shu.billing.router.BillingStateService.update", AsyncMock()) as mock_update,
        ):
            response = await cancel_trial(user=_mock_admin_user(), db=MagicMock())

        assert response.status_code == 200
        mock_update.assert_awaited_once()
        kwargs = mock_update.await_args.kwargs
        assert kwargs["updates"] == {"subscription_status": "canceled"}
        assert kwargs["source"] == "api:cancel-trial"

    @pytest.mark.asyncio
    async def test_cancel_trial_local_write_failure_does_not_fail_request(self):
        """If the local `subscription_status` write throws, the user still gets
        a 200. The forwarded webhook will catch up and write the same value;
        failing the response would leave the customer thinking the cancel didn't
        take when in fact Stripe has already canceled.
        """
        cache, cp_client = _stub_cp_cache()
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
            patch(
                "shu.billing.router.BillingStateService.update",
                AsyncMock(side_effect=RuntimeError("db went away")),
            ),
        ):
            response = await cancel_trial(user=_mock_admin_user(), db=MagicMock())

        assert response.status_code == 200
        cache.invalidate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_trial_writes_db_before_cache_invalidate(self):
        """Ordering invariant: the local `subscription_status="canceled"` write
        must land before `cache.invalidate()`. Reverse order lets a concurrent
        get() repopulate cache from CP with the pre-cancel state before the DB
        row catches up, defeating the cancel gate in `assert_subscription_active`.
        """
        cache, cp_client = _stub_cp_cache()
        call_order: list[str] = []

        async def record_update(*_args, **_kwargs):
            call_order.append("db_update")

        async def record_invalidate():
            call_order.append("invalidate")

        cache.invalidate = AsyncMock(side_effect=record_invalidate)
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
            patch("shu.billing.router.BillingStateService.update", AsyncMock(side_effect=record_update)),
        ):
            await cancel_trial(user=_mock_admin_user(), db=MagicMock())

        assert call_order == ["db_update", "invalidate"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", _ENDPOINTS)
    async def test_unexpected_exception_emits_audit_log_and_propagates(
        self, endpoint: str, caplog
    ):
        """Catch-all audit log: any exception escaping `cp_call` (programmer
        error, unexpected httpx state, etc.) must still emit a `billing.tier_change`
        error entry before propagating. Without this, destructive billing
        actions could fail without a trail per R12.AC3.
        """
        import logging as _logging

        cache, cp_client = _stub_cp_cache(cp_call_outcome=RuntimeError("kaboom"))
        with (
            patch("shu.billing.router.get_billing_state_cache", return_value=cache),
            patch("shu.billing.router.get_cp_client", return_value=cp_client),
        ):
            with caplog.at_level(_logging.INFO, logger="shu.billing.router"):
                with pytest.raises(RuntimeError):
                    await _call_endpoint(endpoint)

        # The audit entry uses the structured key `billing.tier_change` with
        # the exception class on `error_class`.
        audit_records = [
            r
            for r in caplog.records
            if r.name == "shu.billing.router"
            and r.getMessage() == "billing.tier_change"
            and getattr(r, "error_class", None) == "RuntimeError"
            and getattr(r, "outcome", None) == "error"
        ]
        assert len(audit_records) == 1, (
            f"Expected one catch-all audit entry; got {[r.getMessage() for r in caplog.records]}"
        )
        # Cache was NOT invalidated — the action didn't reach success.
        cache.invalidate.assert_not_called()


# `_resolve_remaining_grant_amount` — local fallback when CP returns
# `remaining_grant_amount=None` during trial (Task 26 / design 3a).
# Direct unit tests against the helper, not through the route handler,
# to keep the branching surface compact.


_P_USAGE_PROVIDER = "shu.billing.router.UsageProviderImpl"


def _trial_state(
    *,
    total_grant: Decimal = Decimal("5.00"),
    usage_markup_multiplier: Decimal | None = Decimal("1.0"),
) -> BillingState:
    # Default markup=1.0 so callers that don't care about the upcharge can
    # write tests in raw-dollar terms. Tests covering the markup path pass
    # an explicit multiplier (or None to verify the configured-default
    # fallback path).
    return BillingState(
        openrouter_key_disabled=False,
        payment_failed_at=None,
        payment_grace_days=0,
        entitlements=EntitlementSet(),
        is_trial=True,
        trial_deadline=datetime(2026, 5, 30, tzinfo=UTC),
        total_grant_amount=total_grant,
        remaining_grant_amount=None,
        seat_price_usd=Decimal("20"),
        usage_markup_multiplier=usage_markup_multiplier,
    )


def _non_trial_state(*, remaining: Decimal = Decimal("12.34")) -> BillingState:
    return BillingState(
        openrouter_key_disabled=False,
        payment_failed_at=None,
        payment_grace_days=0,
        entitlements=EntitlementSet(),
        is_trial=False,
        trial_deadline=None,
        total_grant_amount=Decimal("50"),
        remaining_grant_amount=remaining,
        seat_price_usd=Decimal("20"),
    )


def _stub_usage_provider(*, total_cost_usd: Decimal) -> MagicMock:
    summary = MagicMock()
    summary.total_cost_usd = total_cost_usd
    instance = MagicMock()
    instance.get_usage_summary = AsyncMock(return_value=summary)
    return MagicMock(return_value=instance)


def _stub_billing_row(
    *, period_start: datetime | None = datetime(2026, 5, 1, tzinfo=UTC),
) -> MagicMock:
    row = MagicMock()
    row.current_period_start = period_start
    return row


class TestResolveRemainingGrantAmount:
    """Direct tests for `_resolve_remaining_grant_amount`."""

    @pytest.mark.asyncio
    async def test_non_trial_passes_through_cp_value_unchanged(self):
        """When CP returns a numeric value, the helper does no local math —
        Stripe's grant debiting is accurate for non-trial subscriptions.
        """
        from shu.billing.router import _resolve_remaining_grant_amount

        result = await _resolve_remaining_grant_amount(
            db=AsyncMock(),
            state=_non_trial_state(remaining=Decimal("12.34")),
        )
        assert result == Decimal("12.34")

    @pytest.mark.asyncio
    async def test_trial_with_usage_below_grant(self):
        """Trial: total=$5, usage=$2 → remaining=$3."""
        from shu.billing.router import _resolve_remaining_grant_amount

        with (
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_stub_billing_row())),
            patch(_P_USAGE_PROVIDER, _stub_usage_provider(total_cost_usd=Decimal("2.00"))),
        ):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(total_grant=Decimal("5.00")),
            )
        assert result == Decimal("3.00")

    @pytest.mark.asyncio
    async def test_trial_with_usage_exceeding_grant_clamps_to_zero(self):
        """Defensive — trial-cap should have blocked already, but a state
        desync shouldn't render a negative dollar amount on the banner.
        """
        from shu.billing.router import _resolve_remaining_grant_amount

        with (
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_stub_billing_row())),
            patch(_P_USAGE_PROVIDER, _stub_usage_provider(total_cost_usd=Decimal("999"))),
        ):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(total_grant=Decimal("5.00")),
            )
        assert result == Decimal(0)

    @pytest.mark.asyncio
    async def test_trial_with_no_billing_state_row_falls_back_to_total(self):
        """No period anchor → can't attribute usage to "this period."
        Returning total keeps the banner sensible (full budget showing)
        until the period is established.
        """
        from shu.billing.router import _resolve_remaining_grant_amount

        with patch(_P_BILLING_STATE_GET, AsyncMock(return_value=None)):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(total_grant=Decimal("5.00")),
            )
        assert result == Decimal("5.00")

    @pytest.mark.asyncio
    async def test_trial_with_null_period_start_falls_back_to_total(self):
        """Billing row exists but `current_period_start` is null — same
        recovery as no-row, return the full grant.
        """
        from shu.billing.router import _resolve_remaining_grant_amount

        with patch(
            _P_BILLING_STATE_GET,
            AsyncMock(return_value=_stub_billing_row(period_start=None)),
        ):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(total_grant=Decimal("5.00")),
            )
        assert result == Decimal("5.00")

    @pytest.mark.asyncio
    async def test_trial_applies_markup_when_attached(self):
        """`total_grant_amount` is customer-billed dollars and `total_cost_usd`
        is raw provider cost — without the markup the banner under-counts
        spend. With markup=1.3 and $2 of raw usage on a $5 grant, the banner
        shows $5 - $2*1.3 = $2.40 remaining.
        """
        from shu.billing.router import _resolve_remaining_grant_amount

        with (
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_stub_billing_row())),
            patch(_P_USAGE_PROVIDER, _stub_usage_provider(total_cost_usd=Decimal("2.00"))),
        ):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(
                    total_grant=Decimal("5.00"),
                    usage_markup_multiplier=Decimal("1.3"),
                ),
            )
        assert result == Decimal("2.40")

    @pytest.mark.asyncio
    async def test_trial_falls_back_to_configured_default_when_markup_unset(
        self, monkeypatch
    ):
        """HEALTHY_DEFAULT and no-metered-item paths leave the field None.
        Helper resolves to the configured default rather than zero/error.
        """
        from shu.billing import markup as markup_mod
        from shu.billing.router import _resolve_remaining_grant_amount

        # Pin the configured default so the assertion doesn't drift with
        # any future change to the env-default constant.
        settings = markup_mod.get_billing_settings()
        monkeypatch.setattr(settings, "usage_markup_multiplier_default", Decimal("1.5"))

        with (
            patch(_P_BILLING_STATE_GET, AsyncMock(return_value=_stub_billing_row())),
            patch(_P_USAGE_PROVIDER, _stub_usage_provider(total_cost_usd=Decimal("2.00"))),
        ):
            result = await _resolve_remaining_grant_amount(
                db=AsyncMock(),
                state=_trial_state(
                    total_grant=Decimal("5.00"),
                    usage_markup_multiplier=None,
                ),
            )
        # $5 - $2 * 1.5 = $2.00
        assert result == Decimal("2.00")
