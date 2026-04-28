"""Billing quantity sync — upgrade-only safety net.

Admins drive all seat-count changes explicitly through `SeatService`. This
daily job exists only to catch the edge case where our active user count
has outpaced the Stripe subscription quantity (e.g. an admin upgrade write
failed after the user row landed locally): it bumps Stripe up to match.
Downgrades are never reconciled here — they are admin-scheduled and execute
at period end, so shrinking Stripe automatically would fight live admin
decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.config import get_billing_settings
from shu.billing.stripe_client import find_seat_item
from shu.core.logging import get_logger
from shu.core.queue_backend import QueueBackend

if TYPE_CHECKING:
    from shu.billing.service import BillingService

logger = get_logger(__name__)

# Daily reconciliation interval (24 hours)
_RECONCILIATION_INTERVAL_SECONDS = 86400


async def _fetch_current_stripe_quantity(service: BillingService, subscription_id: str) -> int | None:
    """Return Stripe's live seat quantity, or None if it can't be determined.

    A None result triggers a skip in the caller — on any failure the
    upgrade-only safety net deliberately holds rather than writing blind.
    The broad except covers payload-shape drift in addition to network
    failure: a missing ``items`` key or a non-numeric quantity should both
    skip the cycle, not crash the scheduler thread.
    """
    try:
        subscription = await service._client.get_subscription(subscription_id)
        if subscription is None:
            return None
        seat_item = find_seat_item(subscription)
        if seat_item is None:
            return None
        return int(seat_item["quantity"])
    except Exception:
        logger.warning("Failed to fetch subscription for quantity guard", exc_info=True)
        return None


# =============================================================================
# Scheduler Source (Daily Reconciliation)
# =============================================================================


class BillingQuantitySyncSource:
    """Schedulable source for daily billing quantity reconciliation.

    Follows the same pattern as AttachmentCleanupSource: does work inline
    in cleanup_stale(), throttled by _last_run.
    """

    def __init__(self) -> None:
        self._last_run: datetime | None = None

    @property
    def name(self) -> str:
        return "billing_quantity_sync"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        settings = get_billing_settings()
        if not settings.is_configured:
            return 0

        now = datetime.now(UTC)
        if self._last_run is not None:
            elapsed = (now - self._last_run).total_seconds()
            if elapsed < _RECONCILIATION_INTERVAL_SECONDS:
                return 0

        from shu.billing.adapters import get_active_user_count, get_billing_config
        from shu.billing.service import BillingService

        try:
            billing_config = await get_billing_config(db)
            subscription_id = billing_config.get("stripe_subscription_id")
            if not subscription_id:
                self._last_run = now
                return 0

            user_count = await get_active_user_count(db)
            service = BillingService(settings)

            # We do not auto-downgrade accounts, so we will only sync if there are upgrades.
            # Generally those cases should be handled outside of here as well, but this is a
            # safety net in case something went wrong there.
            current_stripe_qty = await _fetch_current_stripe_quantity(service, subscription_id)
            if current_stripe_qty is None or user_count <= current_stripe_qty:
                logger.debug(
                    "Skipping daily reconciliation — at or below Stripe (upgrade-only)",
                    extra={
                        "subscription_id": subscription_id,
                        "user_count": user_count,
                        "stripe_quantity": current_stripe_qty,
                    },
                )
                self._last_run = now
                return 0

            updated = await service.sync_subscription_quantity(subscription_id, user_count)

            self._last_run = now

            if updated:
                logger.info(
                    "Daily quantity reconciliation synced",
                    extra={"subscription_id": subscription_id, "user_count": user_count},
                )
                return 1

            return 0

        except Exception:
            logger.error("Daily quantity reconciliation failed", exc_info=True)
            # Retry in 5 minutes rather than the full 24-hour interval so transient
            # failures (Stripe outage, DB hiccup) recover quickly.
            self._last_run = now - timedelta(seconds=_RECONCILIATION_INTERVAL_SECONDS - 300)
            return 0

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        return {"enqueued": 0}


class UsageReportingSource:
    """Schedulable source for periodic usage reporting to Stripe Meters.

    Runs at a configurable interval (default 1 hour). On each run, compares
    our llm_usage totals against Stripe's meter summary and sends any gap.
    Self-correcting: missed events from prior runs are caught automatically.
    """

    def __init__(self) -> None:
        self._last_run: datetime | None = None

    @property
    def name(self) -> str:
        return "usage_reporting"

    async def cleanup_stale(self, db: AsyncSession) -> int:
        settings = get_billing_settings()
        if not settings.is_configured:
            return 0

        now = datetime.now(UTC)
        interval = settings.usage_report_interval_seconds
        if self._last_run is not None:
            elapsed = (now - self._last_run).total_seconds()
            if elapsed < interval:
                return 0

        from shu.billing.service import BillingService

        try:
            service = BillingService(settings)
            result = await service.report_and_reconcile_usage(db)
            self._last_run = now

            if result.get("action") == "reported":
                logger.info(
                    "Usage reporting completed",
                    extra={"delta": result.get("delta"), "our_total": result.get("our_total")},
                )
                return 1

            return 0

        except Exception:
            logger.error("Usage reporting failed", exc_info=True)
            # Retry in 5 minutes rather than waiting the full configured interval.
            # Clamp to 0 so an operator-configured interval < 300s (e.g. during
            # testing) doesn't push _last_run into the future and silently
            # disable the job forever.
            backoff = max(interval - 300, 0)
            self._last_run = now - timedelta(seconds=backoff)
            return 0

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        return {"enqueued": 0}
