"""TTL'd cache for the tenant→CP billing-state poll, with stale-while-error.

The cache is the single value consumers see. Policy lives entirely here:
    - Fresh hit (within TTL): return cached value, no fetch.
    - Stale or empty + CP success: update both fields, return new value.
    - Stale + CP failure: keep the last successful value, leave
      `_last_success_at` untouched (so the next call retries on the next
      TTL boundary, not a delayed one), log a warning, return the stale
      value to the caller. No exceptions propagated.
    - Cold start (no prior success) + CP failure: return HEALTHY_DEFAULT.

The fail-open trade-off is deliberate (see SHU-743 Notes): CP outages
should not lock customers out of OCR. Bounded — chat / embedding cost
is gated at OpenRouter, so the leak window is OCR-only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI

from shu.billing.config import get_billing_settings
from shu.billing.cp_client import (
    HEALTHY_DEFAULT,
    BillingState,
    CpClient,
    CpClientError,
)
from shu.core.config import get_settings_instance
from shu.core.http_client import get_http_client
from shu.core.logging import get_logger

_logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BillingStateCache:
    def __init__(
        self,
        client: CpClient,
        ttl_seconds: int,
        logger: logging.Logger,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._logger = logger
        self._clock = clock
        self._value: BillingState | None = None
        self._last_success_at: datetime | None = None
        # Single-flight: concurrent get() callers wait on this lock instead
        # of stampeding CP. The freshness re-check inside the lock prevents
        # waiters from triggering a redundant fetch after the holder succeeds.
        self._lock = asyncio.Lock()

    async def get(self) -> BillingState:
        async with self._lock:
            if self._is_fresh():
                # type-narrowed: _is_fresh implies a prior success, so _value is set.
                assert self._value is not None
                return self._value

            try:
                value = await self._client.fetch_billing_state()
            except CpClientError as exc:
                return self._serve_stale_or_default(exc)

            self._value = value
            self._last_success_at = self._clock()
            return value

    def _is_fresh(self) -> bool:
        if self._value is None or self._last_success_at is None:
            return False
        age = (self._clock() - self._last_success_at).total_seconds()
        return age < self._ttl_seconds

    def _serve_stale_or_default(self, exc: CpClientError) -> BillingState:
        # Note: _last_success_at is deliberately NOT advanced here. Advancing
        # it would push the next retry out by another full TTL window even
        # though we never got fresh state — the goal is to retry on the next
        # boundary based on the LAST successful fetch, not the last attempt.
        if self._value is not None:
            self._logger.warning(
                "CP unreachable, serving stale billing state",
                extra={
                    "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
                    "exc_type": type(exc).__name__,
                },
            )
            return self._value
        self._logger.warning(
            "CP unreachable on cold start, serving healthy default",
            extra={"exc_type": type(exc).__name__},
        )
        return HEALTHY_DEFAULT


async def initialize_billing_state_cache(app: FastAPI) -> None:
    """Eager-load the cache so SHU-703 enforcement (when it lands) sees a
    fresh value on the first request rather than the cold-start fallback.

    Skipped on self-hosted / dev deployments where CP isn't configured —
    presence of all three of `tenant_id`, `router_shared_secret`, and
    `cp_base_url` is the signal that the tenant is meant to talk to CP.
    """
    settings = get_settings_instance()
    billing_settings = get_billing_settings()
    if not (settings.tenant_id and billing_settings.router_shared_secret and billing_settings.cp_base_url):
        _logger.info(
            "CP billing-state cache disabled — tenant_id, router secret, or "
            "CP base URL not configured (self-hosted / dev)"
        )
        return

    try:
        http_client = await get_http_client()
        client = CpClient(
            base_url=billing_settings.cp_base_url,
            tenant_id=UUID(settings.tenant_id),
            shared_secret=billing_settings.router_shared_secret,
            http_client=http_client,
            logger=get_logger("shu.billing.cp_client"),
        )
        cache = BillingStateCache(
            client=client,
            ttl_seconds=billing_settings.billing_state_cache_ttl_seconds,
            logger=get_logger("shu.billing.billing_state_cache"),
        )
        # Eager fetch absorbs CP unreachability via stale-while-error, so this
        # never raises a CpClientError. Any exception here is a misconfiguration
        # (bad UUID, broken httpx, etc.) caught by the outer try/except.
        await cache.get()
        app.state.billing_state_cache = cache
        _logger.info("CP billing-state cache initialized: %s", await cache.get())
    except Exception as e:
        _logger.warning(f"CP billing-state cache initialization failed: {e}")
