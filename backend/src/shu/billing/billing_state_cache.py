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

from shu.billing.billing_state_persister import BillingStatePersister
from shu.billing.config import get_billing_settings
from shu.billing.cp_client import (
    HEALTHY_DEFAULT,
    BillingState,
    CpClient,
    CpClientError,
)
from shu.core.http_client import get_http_client
from shu.core.logging import get_logger
from shu.core.tenant import tenant_context

_logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BillingStateCache:
    def __init__(
        self,
        client: CpClient,
        ttl_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
        persister: BillingStatePersister | None = None,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # Optional disk-backed fallback. When set, successful polls are
        # mirrored to system_settings and a cold-start CP failure attempts
        # to restore from there before falling back to HEALTHY_DEFAULT.
        # Tests pass None to stay DB-free.
        self._persister = persister
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
                return await self._serve_stale_or_default(exc)

            self._value = value
            self._last_success_at = self._clock()
            if self._persister is not None:
                await self._persister.save(value)
            return value

    async def invalidate(self) -> None:
        # Acquired under the same lock as get() so an in-flight fetch can't
        # write its result back AFTER we've cleared state — that would
        # silently restore the stale value the admin action just superseded.
        # Clearing the failure-backoff window too: an admin upgrade right
        # after a CP blip should re-poll on the next read, not wait out
        # the backoff timer.
        async with self._lock:
            self._value = None
            self._last_success_at = None
            self._next_retry_after = None

    def _is_fresh(self) -> bool:
        if self._value is None or self._last_success_at is None:
            return False
        age = (self._clock() - self._last_success_at).total_seconds()
        if age >= self._ttl_seconds:
            return False
        # Once we cross the wire's stated current_period_end, the cached value
        # is stale by definition — Stripe has rolled (period rollover, trial
        # conversion, etc.). Force ONE refresh past the boundary; the guard
        # (last_success_at < period_end) prevents a tight loop if CP hasn't
        # yet processed Stripe's rollover webhook when we refetch.
        period_end = self._value.current_period_end
        crossed_period_boundary = period_end is not None and self._last_success_at < period_end <= self._clock()
        return not crossed_period_boundary

    def _is_in_failure_backoff(self) -> bool:
        return self._next_retry_after is not None and self._clock() < self._next_retry_after

    async def _serve_stale_or_default(self, exc: CpClientError) -> BillingState:
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
        # Cold start with no in-memory value: try the persisted last-known
        # state before falling back to HEALTHY_DEFAULT. A restart that
        # coincides with a CP outage shouldn't drop a paying tenant to the
        # trial-cap-blocked default if we have their prior state.
        if self._persister is not None:
            persisted = await self._persister.load()
            if persisted is not None:
                _logger.warning(
                    "CP unreachable on cold start, restored billing state from disk",
                    extra={"exc_type": type(exc).__name__},
                )
                # Populate as if it were a fresh fetch — _last_success_at
                # left unset so the next call past the failure backoff
                # retries CP rather than treating disk-restored data as
                # eternally fresh.
                self._value = persisted
                return persisted
        _logger.warning(
            "CP unreachable on cold start, serving healthy default",
            extra={"exc_type": type(exc).__name__},
        )
        return HEALTHY_DEFAULT


# Per-tenant cache + CpClient maps. Each tenant_id gets its own
# BillingStateCache (with its own CpClient and persister), lazy-populated
# on the first request that lands for a tenant we haven't seen before;
# new tenants provisioned after process start are picked up automatically.
#
# ``None`` is a valid value in ``_cache_by_tenant`` meaning "we determined
# CP isn't configured / build failed for this tenant — don't keep retrying
# every request".
#
# The cache and the CpClient are siblings: the cache owns state + freshness
# policy; the client owns transport. The router uses the client directly
# for trial-action POSTs (upgrade-now, cancel-subscription) rather than
# reaching through the cache, so a future change to the cache's internals
# can't accidentally break the trial-action wiring. Both maps are kept in
# lock-step — when one tenant's cache lands, its CpClient lands too.
#
# Kept as module state rather than `app.state` because the worker has no
# FastAPI app — gating OCR jobs at dequeue time (SHU-703) needs reachability
# from a non-FastAPI context.
_cache_by_tenant: dict[str, BillingStateCache | None] = {}
_cp_client_by_tenant: dict[str, CpClient] = {}


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
    tid = tenant_context.get(None)
    if tid is None:
        return None
    if tid in _cache_by_tenant:
        return _cache_by_tenant[tid]

    billing_settings = get_billing_settings()
    if not (billing_settings.router_shared_secret and billing_settings.cp_base_url):
        # Definitively no CP configured for this deployment — memoize so future
        # requests skip the recheck. Distinguished from the exception branch
        # below: this is steady-state config, not a transient build error.
        _cache_by_tenant[tid] = None
        return None

    # No lock: two concurrent first-requests for the same tenant might both
    # build a cache, but they're equivalent — the second writer wins on the
    # dict slot and the first cache is GC'd. Single wasted CP call in a rare
    # case, vs. the loop-binding hazards of a module-level asyncio.Lock.
    try:
        http_client = await get_http_client()
        # Build the CpClient and the cache together; both maps publish in
        # lock-step so trial-action callers reading get_cp_client() expect
        # the matching cache to be ready too.
        cp_client = CpClient(
            base_url=billing_settings.cp_base_url,
            tenant_id=UUID(tid),
            shared_secret=billing_settings.router_shared_secret,
            http_client=http_client,
            logger=get_logger("shu.billing.cp_client"),
        )
        cache = BillingStateCache(
            client=cp_client,
            ttl_seconds=billing_settings.billing_state_cache_ttl_seconds,
            persister=BillingStatePersister(),
        )
        _cp_client_by_tenant[tid] = cp_client
        _cache_by_tenant[tid] = cache
        return cache
    except Exception as e:
        # A transient blip during build (DNS hiccup, httpx pool exhaustion,
        # something flaky) used to write None into the slot and permanently
        # disable enforcement for this tenant until process restart — operator
        # had no way to tell "configured-off" from "stuck-after-blip". We now
        # leave the slot empty so the next caller retries the build. Cost:
        # subsequent failing requests during a sustained outage each do one
        # CP-client construction attempt before failing closed; a circuit-
        # breaker can be added later if benchmarks warrant.
        _logger.warning("CP billing-state cache build failed for tenant %s: %s", tid, e)
        return None


async def get_cp_client() -> CpClient | None:
    """Return the CpClient for the current tenant, building lazily.

    Used by admin trial-action endpoints (upgrade-now, cancel-trial) that
    POST to CP outside the billing-state poll path. Routes through
    ``get_billing_state_cache`` so the cache and the client land together
    in their respective per-tenant maps.
    """
    tid = tenant_context.get(None)
    if tid is None:
        return None
    if tid in _cp_client_by_tenant:
        return _cp_client_by_tenant[tid]
    # Building the cache builds the client too.
    await get_billing_state_cache()
    return _cp_client_by_tenant.get(tid)


def reset_billing_state_cache() -> None:
    """Reset all per-tenant caches + CpClient maps. Test-only."""
    global _cache_by_tenant, _cp_client_by_tenant  # noqa: PLW0603
    _cache_by_tenant = {}
    _cp_client_by_tenant = {}
