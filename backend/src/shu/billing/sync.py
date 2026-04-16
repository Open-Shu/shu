"""Billing quantity sync — real-time triggers and daily reconciliation.

Keeps the Stripe subscription quantity in sync with the actual user count
for per-seat billing. Two mechanisms:

1. `trigger_quantity_sync()` — fire-and-forget helper called from user
   create/delete endpoints. Owns its own DB session so it's safe to run
   as an `asyncio.create_task`.

2. `BillingQuantitySyncSource` — scheduler source that runs daily to
   reconcile any drift (missed events, Stripe API failures, etc.).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from shu.billing.config import get_billing_settings
from shu.core.logging import get_logger
from shu.core.queue_backend import QueueBackend

logger = get_logger(__name__)

# Daily reconciliation interval (24 hours)
_RECONCILIATION_INTERVAL_SECONDS = 86400


async def trigger_quantity_sync() -> None:
    """Sync Stripe subscription quantity to match current user count.

    Safe for fire-and-forget: creates its own DB session, catches all
    exceptions, never raises. Call via `asyncio.create_task(trigger_quantity_sync())`.
    """
    # Fast check — no DB needed
    settings = get_billing_settings()
    if not settings.is_configured:
        return

    from shu.billing.adapters import get_active_user_count, get_billing_config
    from shu.billing.service import BillingService
    from shu.core.database import get_db_session

    db: AsyncSession | None = None
    try:
        db = await get_db_session()
        billing_config = await get_billing_config(db)
        subscription_id = billing_config.get("stripe_subscription_id")
        if not subscription_id:
            logger.debug("No subscription configured, skipping quantity sync")
            return

        user_count = await get_active_user_count(db)
        service = BillingService(settings)
        updated = await service.sync_subscription_quantity(subscription_id, user_count)

        # Always persist the local quantity so billing_state stays in sync
        # even when Stripe already had the right value (updated=False).
        from shu.billing.state_service import BillingStateService

        await BillingStateService.update(
            db,
            updates={"quantity": user_count},
            source="scheduler:quantity_sync",
        )

        if updated:
            logger.info(
                "Quantity sync completed",
                extra={"subscription_id": subscription_id, "user_count": user_count},
            )
    except Exception:
        logger.error("Quantity sync failed", exc_info=True)
    finally:
        if db is not None:
            await db.close()


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
            updated = await service.sync_subscription_quantity(subscription_id, user_count)

            # Always persist the local quantity so billing_state stays current
            # even when Stripe already had the right value (updated=False).
            # sync_subscription_quantity raises on error (not-found, no seat item),
            # so reaching here means Stripe confirmed the subscription is healthy.
            # _last_run is advanced only after the DB write succeeds so that a
            # DB failure after a successful Stripe update does not silence the
            # discrepancy for the full reconciliation interval.
            from shu.billing.state_service import BillingStateService

            await BillingStateService.update(
                db,
                updates={"quantity": user_count},
                source="scheduler:daily_quantity_reconciliation",
            )
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
            self._last_run = now - timedelta(seconds=interval - 300)
            return 0

    async def enqueue_due(self, db: AsyncSession, queue: QueueBackend, *, limit: int) -> dict[str, int]:
        return {"enqueued": 0}
