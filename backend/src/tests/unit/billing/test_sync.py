"""Tests for billing sync — BillingQuantitySyncSource and UsageReportingSource."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.sync import BillingQuantitySyncSource, UsageReportingSource

# sync.py uses deferred imports inside function bodies, so we must patch
# at the source modules rather than shu.billing.sync.*.
_P_SETTINGS = "shu.billing.sync.get_billing_settings"
_P_BILLING_CONFIG = "shu.billing.adapters.get_billing_config"
_P_USER_COUNT = "shu.billing.adapters.get_active_user_count"
_P_SERVICE = "shu.billing.service.BillingService"
_P_FETCH_QTY = "shu.billing.sync._fetch_current_stripe_quantity"


def _make_unconfigured_settings():
    settings = MagicMock()
    settings.is_configured = False
    return settings


def _make_configured_settings():
    settings = MagicMock()
    settings.is_configured = True
    settings.secret_key = "sk_test_fake"
    return settings


class TestBillingQuantitySyncSource:
    """Tests for the daily reconciliation scheduler source."""

    @pytest.mark.asyncio
    async def test_skips_when_not_configured(self):
        """Should return 0 when billing is not configured."""
        source = BillingQuantitySyncSource()

        with patch(_P_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = _make_unconfigured_settings()
            result = await source.cleanup_stale(AsyncMock())

        assert result == 0

    @pytest.mark.asyncio
    async def test_throttles_by_interval(self):
        """Should skip if last run was within the interval."""
        source = BillingQuantitySyncSource()
        source._last_run = datetime.now(UTC)

        with patch(_P_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = _make_configured_settings()
            result = await source.cleanup_stale(AsyncMock())

        assert result == 0

    @pytest.mark.asyncio
    async def test_upgrades_when_user_count_exceeds_stripe_quantity(self):
        """user_count > stripe_qty → call sync_subscription_quantity to match."""
        source = BillingQuantitySyncSource()
        mock_db = AsyncMock()

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_CONFIG) as mock_get_config,
            patch(_P_USER_COUNT) as mock_get_count,
            patch(_P_SERVICE) as mock_svc_cls,
            patch(_P_FETCH_QTY, new_callable=AsyncMock) as mock_fetch_qty,
            patch(
                "shu.billing.state_service.BillingStateService.update",
                new_callable=AsyncMock,
            ) as mock_state_update,
        ):
            mock_get_settings.return_value = _make_configured_settings()
            mock_get_config.return_value = {"stripe_subscription_id": "sub_123"}
            mock_get_count.return_value = 5
            mock_service = MagicMock()
            mock_service.sync_subscription_quantity = AsyncMock(return_value=True)
            mock_svc_cls.return_value = mock_service
            mock_fetch_qty.return_value = 3  # Stripe has 3, we have 5 active → upgrade

            result = await source.cleanup_stale(mock_db)

        assert result == 1
        mock_service.sync_subscription_quantity.assert_awaited_once_with("sub_123", 5)
        mock_state_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_when_user_count_equals_stripe_quantity(self):
        """user_count == stripe_qty → skip. No Stripe write."""
        source = BillingQuantitySyncSource()
        mock_db = AsyncMock()

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_CONFIG) as mock_get_config,
            patch(_P_USER_COUNT) as mock_get_count,
            patch(_P_SERVICE) as mock_svc_cls,
            patch(_P_FETCH_QTY, new_callable=AsyncMock) as mock_fetch_qty,
        ):
            mock_get_settings.return_value = _make_configured_settings()
            mock_get_config.return_value = {"stripe_subscription_id": "sub_123"}
            mock_get_count.return_value = 3
            mock_service = MagicMock()
            mock_service.sync_subscription_quantity = AsyncMock(return_value=False)
            mock_svc_cls.return_value = mock_service
            mock_fetch_qty.return_value = 3  # parity

            result = await source.cleanup_stale(mock_db)

        assert result == 0
        mock_service.sync_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_on_downgrade(self):
        """Should skip when user_count < Stripe quantity — safety net is upgrade-only.

        Downgrades are admin-scheduled through SeatService + the SHU-704 primitive;
        the reconciler must not shrink Stripe on its own.
        """
        source = BillingQuantitySyncSource()
        assert source._last_run is None

        mock_db = AsyncMock()

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_CONFIG) as mock_get_config,
            patch(_P_USER_COUNT) as mock_get_count,
            patch(_P_SERVICE) as mock_svc_cls,
            patch(_P_FETCH_QTY, new_callable=AsyncMock) as mock_fetch_qty,
        ):
            mock_get_settings.return_value = _make_configured_settings()
            mock_get_config.return_value = {"stripe_subscription_id": "sub_123"}
            mock_get_count.return_value = 2
            mock_service = MagicMock()
            mock_service.sync_subscription_quantity = AsyncMock(return_value=True)
            mock_svc_cls.return_value = mock_service
            mock_fetch_qty.return_value = 5  # Stripe has 5 seats, we have 2 active users

            result = await source.cleanup_stale(mock_db)

        assert result == 0
        assert source._last_run is not None
        mock_service.sync_subscription_quantity.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_due_returns_zero(self):
        """enqueue_due should always return 0 — work happens in cleanup_stale."""
        source = BillingQuantitySyncSource()
        result = await source.enqueue_due(AsyncMock(), AsyncMock(), limit=10)
        assert result == {"enqueued": 0}


_P_BILLING_SERVICE = "shu.billing.service.BillingService"


class TestUsageReportingSource:
    """Tests for the hourly usage reporting scheduler source."""

    @pytest.mark.asyncio
    async def test_skips_when_not_configured(self):
        """Should return 0 when billing is not configured."""
        source = UsageReportingSource()

        with patch(_P_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = _make_unconfigured_settings()
            result = await source.cleanup_stale(AsyncMock())

        assert result == 0

    @pytest.mark.asyncio
    async def test_throttles_by_interval(self):
        """Should skip if last run was within the interval."""
        source = UsageReportingSource()
        source._last_run = datetime.now(UTC)

        settings = _make_configured_settings()
        settings.usage_report_interval_seconds = 3600

        with patch(_P_SETTINGS) as mock_get_settings:
            mock_get_settings.return_value = settings
            result = await source.cleanup_stale(AsyncMock())

        assert result == 0

    @pytest.mark.asyncio
    async def test_runs_and_reports(self):
        """Should call report_and_reconcile_usage and return 1 on success."""
        source = UsageReportingSource()
        assert source._last_run is None

        settings = _make_configured_settings()
        settings.usage_report_interval_seconds = 3600

        mock_db = AsyncMock()

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_SERVICE) as mock_svc_cls,
        ):
            mock_get_settings.return_value = settings
            mock_service = MagicMock()
            mock_service.report_and_reconcile_usage = AsyncMock(
                return_value={"action": "reported", "delta": 5000, "our_total": 50000}
            )
            mock_svc_cls.return_value = mock_service

            result = await source.cleanup_stale(mock_db)

        assert result == 1
        assert source._last_run is not None
        mock_service.report_and_reconcile_usage.assert_awaited_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_delta(self):
        """Should return 0 when reconciliation finds no delta."""
        source = UsageReportingSource()

        settings = _make_configured_settings()
        settings.usage_report_interval_seconds = 3600

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_SERVICE) as mock_svc_cls,
        ):
            mock_get_settings.return_value = settings
            mock_service = MagicMock()
            mock_service.report_and_reconcile_usage = AsyncMock(
                return_value={"action": "no_delta"}
            )
            mock_svc_cls.return_value = mock_service

            result = await source.cleanup_stale(AsyncMock())

        assert result == 0

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        """Should catch errors and set _last_run to avoid retry storm."""
        source = UsageReportingSource()

        settings = _make_configured_settings()
        settings.usage_report_interval_seconds = 3600

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_SERVICE) as mock_svc_cls,
        ):
            mock_get_settings.return_value = settings
            mock_service = MagicMock()
            mock_service.report_and_reconcile_usage = AsyncMock(
                side_effect=RuntimeError("Stripe exploded")
            )
            mock_svc_cls.return_value = mock_service

            result = await source.cleanup_stale(AsyncMock())

        assert result == 0
        assert source._last_run is not None

    @pytest.mark.asyncio
    async def test_enqueue_due_returns_zero(self):
        """enqueue_due should always return 0."""
        source = UsageReportingSource()
        result = await source.enqueue_due(AsyncMock(), AsyncMock(), limit=10)
        assert result == {"enqueued": 0}
