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

from shu.billing.cp_client import HEALTHY_DEFAULT, BillingState
from shu.billing.entitlements import EntitlementSet
from shu.billing.router import get_subscription_status

_P_BILLING_CONFIG = "shu.billing.router.get_billing_config"
_P_USER_COUNT = "shu.billing.router.get_active_user_count"


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
