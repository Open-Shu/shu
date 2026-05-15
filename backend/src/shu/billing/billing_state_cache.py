"""TTL'd cache for the tenant→CP billing-state poll, with stale-while-error.

The cache is the single value consumers see. Policy lives entirely here:
    - Fresh hit (within TTL): return cached value, no fetch.
    - Stale or empty + CP success: update both fields, return new value.
    - Stale + CP failure: serve cached (or HEALTHY_DEFAULT on cold start),
      and arm a one-TTL backoff window. While the backoff is armed, no
      further CP fetches happen — every get() short-circuits to the
      cached value or default. Without this, every call past the
      success-TTL would re-enter the fetch branch and serialize 5s
      timeouts under the lock during a CP outage.
    - Cold start (no prior success) + CP failure: return HEALTHY_DEFAULT.

The fail-open trade-off is deliberate (see SHU-743 Notes): CP outages
should not lock customers out of OCR. Bounded — chat / embedding cost
is gated at OpenRouter, so the leak window is OCR-only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from shu.billing.config import get_billing_settings
from shu.billing.cp_client import (
    HEALTHY_DEFAULT,
    BillingState,
    CpClient,
    CpClientError,
)
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
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._value: BillingState | None = None
        self._last_success_at: datetime | None = None
        # Set on every failed fetch attempt to `now + ttl`. While the clock
        # is before this deadline, get() short-circuits without hitting CP —
        # capping the failure-retry rate at one CP attempt per TTL during
        # an outage. Without it, every call past the success-TTL would
        # re-enter the fetch branch and serialize 5s timeouts under the lock.
        self._next_retry_after: datetime | None = None
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
            if self._is_in_failure_backoff():
                return self._value if self._value is not None else HEALTHY_DEFAULT

            try:
                value = await self._client.fetch_billing_state()
            except CpClientError as exc:
                self._next_retry_after = self._clock() + timedelta(seconds=self._ttl_seconds)
                return self._serve_stale_or_default(exc)

            self._value = value
            self._last_success_at = self._clock()
            return value

    def _is_fresh(self) -> bool:
        if self._value is None or self._last_success_at is None:
            return False
        age = (self._clock() - self._last_success_at).total_seconds()
        return age < self._ttl_seconds

    def _is_in_failure_backoff(self) -> bool:
        return self._next_retry_after is not None and self._clock() < self._next_retry_after

    def _serve_stale_or_default(self, exc: CpClientError) -> BillingState:
        # _last_success_at is deliberately NOT advanced on failure; the retry
        # cadence is bounded by _next_retry_after instead.
        if self._value is not None:
            _logger.warning(
                "CP unreachable, serving stale billing state",
                extra={
                    "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
                    "exc_type": type(exc).__name__,
                },
            )
            return self._value
        _logger.warning(
            "CP unreachable on cold start, serving healthy default",
            extra={"exc_type": type(exc).__name__},
        )
        return HEALTHY_DEFAULT


# Per-tenant cache map. Each tenant_id gets its own BillingStateCache (and
# therefore its own CpClient with its own tenant_id baked in). Lazy-populated
# on the first request that lands for a tenant we haven't seen before; new
# tenants provisioned after process start are picked up automatically.
#
# ``None`` is a valid value meaning "we determined CP isn't configured /
# build failed for this tenant — don't keep retrying every request".
#
# Kept as module state rather than `app.state` because the worker has no
# FastAPI app — gating OCR jobs at dequeue time (SHU-703) needs reachability
# from a non-FastAPI context.
_cache_by_tenant: dict[str, BillingStateCache | None] = {}


async def get_billing_state_cache() -> BillingStateCache | None:
    """Return the cache for the current tenant_context, building lazily on first hit.

    Returns ``None`` when:
    - ``tenant_context`` is not set (caller is outside a request handler and
      outside the worker dispatch wrapper). Treat as "enforcement disabled".
    - CP isn't configured for this deployment (``router_shared_secret`` or
      ``cp_base_url`` missing). Also enforcement-disabled.

    The shared envelope secret is intentional: all tenants on a deployment
    sign with the same ``router_shared_secret``. See design note on
    per-tenant secrets — a future change would add a per-tenant secret table
    and an ``X-Shu-Tenant-Id`` envelope header for upfront verification.
    """
    from ..core.tenant import tenant_context

    tid = tenant_context.get(None)
    if tid is None:
        return None
    if tid in _cache_by_tenant:
        return _cache_by_tenant[tid]

    billing_settings = get_billing_settings()
    if not (billing_settings.router_shared_secret and billing_settings.cp_base_url):
        # Remember "no CP configured" so future requests skip the recheck.
        _cache_by_tenant[tid] = None
        return None

    # No lock: two concurrent first-requests for the same tenant might both
    # build a cache, but they're equivalent — the second writer wins on the
    # dict slot and the first cache is GC'd. Single wasted CP call in a rare
    # case, vs. the loop-binding hazards of a module-level asyncio.Lock.
    try:
        http_client = await get_http_client()
        cache = BillingStateCache(
            client=CpClient(
                base_url=billing_settings.cp_base_url,
                tenant_id=UUID(tid),
                shared_secret=billing_settings.router_shared_secret,
                http_client=http_client,
                logger=get_logger("shu.billing.cp_client"),
            ),
            ttl_seconds=billing_settings.billing_state_cache_ttl_seconds,
        )
        _cache_by_tenant[tid] = cache
        return cache
    except Exception as e:
        # Programmer errors (bad UUID, broken httpx, etc.) shouldn't take
        # the request down. Cache the None so we don't retry every request.
        _logger.warning("CP billing-state cache build failed for tenant %s: %s", tid, e)
        _cache_by_tenant[tid] = None
        return None


def reset_billing_state_cache() -> None:
    """Reset all per-tenant caches. Test-only."""
    global _cache_by_tenant  # noqa: PLW0603
    _cache_by_tenant = {}


async def initialize_billing_state_cache() -> None:
    """No-op kept for backward compatibility with lifespan callers.

    Caches are built lazily per-tenant on the first request that resolves to
    each tenant; there's nothing useful to eager-warm at process startup
    because (a) we don't know which tenants will be active yet, and (b) new
    tenants may be provisioned after start. The first request from each
    tenant pays one CP-call latency; subsequent requests hit the cache.
    """
