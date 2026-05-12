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
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from shu.billing.billing_state_persister import BillingStatePersister
from shu.billing.config import get_billing_settings
from shu.billing.cp_client import (
    HEALTHY_DEFAULT,
    BillingState,
    CpClient,
    CpClientError,
)
from shu.billing.stripe_client import StripeClient, StripeClientError
from shu.core.config import get_settings_instance
from shu.core.http_client import get_http_client
from shu.core.logging import get_logger

_logger = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


MarkupFetcher = Callable[[], Awaitable[Decimal | None]]


class BillingStateCache:
    def __init__(
        self,
        client: CpClient,
        ttl_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
        persister: BillingStatePersister | None = None,
        markup_fetcher: MarkupFetcher | None = None,
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # Optional disk-backed fallback. When set, successful polls are
        # mirrored to system_settings and a cold-start CP failure attempts
        # to restore from there before falling back to HEALTHY_DEFAULT.
        # Tests pass None to stay DB-free.
        self._persister = persister
        # Markup is not on the CP wire yet — the tenant attaches it here from
        # Stripe so consumers (trial-cap enforcement, remaining-grant display)
        # see a single BillingState object with everything pre-resolved. When
        # CP starts shipping `usage_markup_multiplier`, drop this fetcher and
        # let the CP value flow through unmodified. See `resolve_markup`.
        self._markup_fetcher = markup_fetcher
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

    @property
    def cp_client(self) -> CpClient:
        """Expose the embedded `CpClient` for admin endpoints that need to
        POST trial-action requests (upgrade-now, cancel-subscription).

        Sharing the cache's client keeps configuration (base_url, secret,
        http_client, logger) in one place rather than re-constructing
        a separate singleton.
        """
        return self._client

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

            # TODO: Change this logic to have CP return the tenant markup.
            value = await self._attach_markup(value)
            self._value = value
            self._last_success_at = self._clock()
            if self._persister is not None:
                await self._persister.save(value)
            return value

    async def _attach_markup(self, value: BillingState) -> BillingState:
        # CP doesn't ship the markup multiplier today, so any value already on
        # the wire is preserved. Skipping the fetch on that branch lets the CP
        # migration land without a coordinated tenant deploy.
        if value.usage_markup_multiplier is not None or self._markup_fetcher is None:
            return value
        try:
            markup = await self._markup_fetcher()
        except StripeClientError as exc:
            # A Stripe blip shouldn't fail the whole refresh — the rest of
            # BillingState is still authoritative. Fall through to the
            # configured default so consumers never see None on a path that
            # otherwise produced a valid CP poll.
            _logger.warning(
                "Stripe markup fetch failed during billing-state refresh; applying configured default",
                extra={"exc_type": type(exc).__name__},
            )
            markup = None
        if markup is None:
            markup = get_billing_settings().usage_markup_multiplier_default
        return dataclasses.replace(value, usage_markup_multiplier=markup)

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
        return age < self._ttl_seconds

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


# Module-level singleton so both the FastAPI app and the worker process
# read the same cache. Kept as module state rather than `app.state` because
# the worker has no FastAPI app — gating OCR jobs at dequeue time
# (SHU-703) needs reachability from a non-FastAPI context.
_cache: BillingStateCache | None = None


def get_billing_state_cache() -> BillingStateCache | None:
    """Return the process-wide cache singleton, or None if CP is not configured.

    Callers must treat None as "self-hosted / dev — enforcement disabled."
    """
    return _cache


def reset_billing_state_cache() -> None:
    """Reset the singleton. Test-only."""
    global _cache  # noqa: PLW0603
    _cache = None


def _build_markup_fetcher(billing_settings) -> MarkupFetcher | None:
    """Build the closure BillingStateCache uses to enrich each refresh.

    Returns None when Stripe isn't fully configured — the cache then leaves
    `usage_markup_multiplier` as whatever CP sent (today: nothing), and
    consumers fall back to the configured default. Constructing the
    StripeClient once at init time avoids per-refresh setup cost and keeps
    `stripe.api_key` stable across the process.
    """
    if not (billing_settings.secret_key and billing_settings.subscription_id):
        return None
    stripe_client = StripeClient(billing_settings)
    subscription_id = billing_settings.subscription_id

    async def fetch() -> Decimal | None:
        return await stripe_client.get_subscription_markup_multiplier(subscription_id)

    return fetch


async def initialize_billing_state_cache() -> None:
    """Eager-load the cache so SHU-703 enforcement sees a fresh value on the
    first request rather than the cold-start fallback.

    Skipped on self-hosted / dev deployments where CP isn't configured —
    presence of all three of `tenant_id`, `router_shared_secret`, and
    `cp_base_url` is the signal that the tenant is meant to talk to CP.

    Idempotent: a second call is a no-op once the singleton is populated,
    so FastAPI startup and worker startup can both invoke this safely.
    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return

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
        cache = BillingStateCache(
            client=CpClient(
                base_url=billing_settings.cp_base_url,
                tenant_id=UUID(settings.tenant_id),
                shared_secret=billing_settings.router_shared_secret,
                http_client=http_client,
                logger=get_logger("shu.billing.cp_client"),
            ),
            ttl_seconds=billing_settings.billing_state_cache_ttl_seconds,
            persister=BillingStatePersister(),
            markup_fetcher=_build_markup_fetcher(billing_settings),
        )
        # Publish the singleton before the eager fetch so a programmer
        # error in get() still leaves the cache reachable. CpClientError
        # is absorbed inside get(); only programmer errors (bad UUID,
        # broken httpx, etc.) escape here.
        _cache = cache
        _logger.info("CP billing-state cache initialized: %s", await cache.get())
    except Exception as e:
        _logger.warning(f"CP billing-state cache initialization failed: {e}")
