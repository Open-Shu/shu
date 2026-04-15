"""Tests for billing sync — trigger_quantity_sync and BillingQuantitySyncSource."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.sync import BillingQuantitySyncSource, UsageReportingSource, trigger_quantity_sync

# sync.py uses deferred imports inside function bodies, so we must patch
# at the source modules rather than shu.billing.sync.*.
_P_SETTINGS = "shu.billing.sync.get_billing_settings"
_P_DB_SESSION = "shu.core.database.get_db_session"
_P_BILLING_CONFIG = "shu.billing.adapters.get_billing_config"
_P_USER_COUNT = "shu.billing.adapters.get_user_count"
_P_SERVICE = "shu.billing.service.BillingService"
_P_SS_SERVICE = "shu.services.system_settings_service.SystemSettingsService"


def _make_unconfigured_settings():
    settings = MagicMock()
    settings.is_configured = False
    return settings


def _make_configured_settings():
    settings = MagicMock()
    settings.is_configured = True
    settings.secret_key = "sk_test_fake"
    return settings


class TestTriggerQuantitySync:
    """Tests for the fire-and-forget sync helper."""

    @pytest.mark.asyncio
    @patch(_P_SETTINGS)
    async def test_returns_early_when_not_configured(self, mock_get_settings):
        """Should do nothing when billing is not configured."""
        mock_get_settings.return_value = _make_unconfigured_settings()

        await trigger_quantity_sync()

        # Should not attempt DB access (no error = success)

    @pytest.mark.asyncio
    @patch(_P_BILLING_CONFIG)
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_returns_early_when_no_subscription(
        self, mock_get_settings, mock_get_db, mock_get_config
    ):
        """Should skip when no subscription ID in billing config."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_get_config.return_value = {"stripe_customer_id": "cus_123"}

        await trigger_quantity_sync()

        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(_P_SERVICE)
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_calls_sync_when_subscription_exists(
        self, mock_get_settings, mock_get_db, mock_get_config, mock_get_count, mock_svc_cls
    ):
        """Should call sync_subscription_quantity when billing is configured."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_get_config.return_value = {
            "stripe_customer_id": "cus_123",
            "stripe_subscription_id": "sub_456",
        }
        mock_get_count.return_value = 5

        mock_service = MagicMock()
        mock_service.sync_subscription_quantity = AsyncMock(return_value=False)
        mock_svc_cls.return_value = mock_service

        await trigger_quantity_sync()

        mock_service.sync_subscription_quantity.assert_awaited_once_with("sub_456", 5)
        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("shu.billing.state_service.BillingStateService.update", new_callable=AsyncMock)
    @patch(_P_SERVICE)
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_persists_quantity_on_update(
        self, mock_get_settings, mock_get_db, mock_get_config, mock_get_count,
        mock_svc_cls, mock_billing_update
    ):
        """Should write updated quantity to billing_state when Stripe quantity changes."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_get_config.return_value = {
            "stripe_customer_id": "cus_123",
            "stripe_subscription_id": "sub_456",
            "quantity": 3,
        }
        mock_get_count.return_value = 5

        mock_service = MagicMock()
        mock_service.sync_subscription_quantity = AsyncMock(return_value=True)
        mock_svc_cls.return_value = mock_service

        await trigger_quantity_sync()

        mock_billing_update.assert_awaited_once()
        _, kwargs = mock_billing_update.call_args
        assert kwargs["updates"] == {"quantity": 5}
        assert kwargs["source"] == "scheduler:quantity_sync"

    @pytest.mark.asyncio
    @patch(_P_BILLING_CONFIG)
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_swallows_exceptions(self, mock_get_settings, mock_get_db, mock_get_config):
        """Should catch and log errors, never raise."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_get_config.side_effect = RuntimeError("DB exploded")

        await trigger_quantity_sync()

        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_swallows_session_creation_exceptions(self, mock_get_settings, mock_get_db):
        """Should catch session acquisition errors and never raise."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_get_db.side_effect = RuntimeError("Session factory exploded")

        await trigger_quantity_sync()

        mock_get_db.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(_P_USER_COUNT)
    @patch(_P_BILLING_CONFIG)
    @patch(_P_DB_SESSION)
    @patch(_P_SETTINGS)
    async def test_skips_when_zero_users(
        self, mock_get_settings, mock_get_db, mock_get_config, mock_get_count
    ):
        """Should skip sync when user count is 0."""
        mock_get_settings.return_value = _make_configured_settings()
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        mock_get_config.return_value = {"stripe_subscription_id": "sub_456"}
        mock_get_count.return_value = 0

        await trigger_quantity_sync()

        mock_db.close.assert_awaited_once()


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
    async def test_runs_on_first_tick(self):
        """Should run on the first tick (no _last_run set)."""
        source = BillingQuantitySyncSource()
        assert source._last_run is None

        mock_db = AsyncMock()

        with (
            patch(_P_SETTINGS) as mock_get_settings,
            patch(_P_BILLING_CONFIG) as mock_get_config,
            patch(_P_USER_COUNT) as mock_get_count,
            patch(_P_SERVICE) as mock_svc_cls,
        ):
            mock_get_settings.return_value = _make_configured_settings()
            mock_get_config.return_value = {"stripe_subscription_id": "sub_123"}
            mock_get_count.return_value = 3
            mock_service = MagicMock()
            mock_service.sync_subscription_quantity = AsyncMock(return_value=False)
            mock_svc_cls.return_value = mock_service

            result = await source.cleanup_stale(mock_db)

        assert result == 0  # No update needed (sync returned False)
        assert source._last_run is not None

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
