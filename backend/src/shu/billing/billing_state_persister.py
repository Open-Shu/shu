"""Disk-backed fallback for `BillingStateCache` cold-start recovery.

Why this exists: a process restart that coincides with a CP outage would
otherwise drop the cache to `HEALTHY_DEFAULT`. With trial-cap fail-closed,
that blocks chat for paying customers we *did* know about — we have their
prior state, we just lost it on restart. Persisting the last successful
poll to `system_settings` lets us restore it when CP isn't reachable.

Out of scope (deliberate): no max-age, no signature/HMAC. Operator-tier
threat model — anyone with DB write access already owns the deployment.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shu.billing.cp_client import _BILLING_STATE_ADAPTER, BillingState
from shu.core.database import get_async_session_local
from shu.core.logging import get_logger
from shu.services.system_settings_service import SystemSettingsService

_logger = get_logger(__name__)

# Stable settings key. Renaming this strands the prior value, so don't.
PERSIST_KEY = "cp_billing_state_cache_last_value"


SessionFactory = Callable[[], async_sessionmaker[AsyncSession]]


class BillingStatePersister:
    """Save/load the last successful CP poll to `system_settings`."""

    def __init__(self, session_factory: SessionFactory = get_async_session_local) -> None:
        # Indirection for tests — production uses the global session factory.
        self._session_factory = session_factory

    async def save(self, state: BillingState) -> None:
        # mode="json" so Decimal/datetime serialize to JSON-friendly forms
        # the system_settings JSON column can store and we can round-trip.
        payload: dict[str, Any] = _BILLING_STATE_ADAPTER.dump_python(state, mode="json")
        try:
            session_local = self._session_factory()
            async with session_local() as db:
                await SystemSettingsService(db).upsert(PERSIST_KEY, payload)
        except Exception:
            # Persistence is best-effort; the in-memory value is already
            # correct and a write failure shouldn't take down the request.
            _logger.warning("failed to persist billing state to system_settings", exc_info=True)

    async def load(self) -> BillingState | None:
        try:
            session_local = self._session_factory()
            async with session_local() as db:
                value = await SystemSettingsService(db).get_value(PERSIST_KEY)
        except Exception:
            _logger.warning("failed to load persisted billing state from system_settings", exc_info=True)
            return None
        if value is None:
            return None
        try:
            return _BILLING_STATE_ADAPTER.validate_python(value)
        except ValidationError:
            # A persisted blob from before a schema change won't validate.
            # Drop it on the floor — caller falls back to HEALTHY_DEFAULT —
            # rather than crashing the boot path on stale data.
            _logger.warning(
                "persisted billing state failed schema validation; ignoring",
                exc_info=True,
            )
            return None


__all__ = ["PERSIST_KEY", "BillingStatePersister"]
