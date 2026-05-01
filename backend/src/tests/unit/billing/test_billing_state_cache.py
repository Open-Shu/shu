"""Tests for shu.billing.billing_state_cache — TTL + stale-while-error policy.

The cache is the single value consumers see, so all policy branches need
explicit coverage: fresh hit, expiry, single-flight, cold-start failure,
warm-cache failure (stale-while-error), recovery, and the load-bearing
invariant that `_last_success_at` is NOT advanced on failure (so retries
land on the next TTL boundary, not a delayed one).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.billing.billing_state_cache import BillingStateCache
from shu.billing.cp_client import (
    HEALTHY_DEFAULT,
    BillingState,
    CpAuthFailed,
    CpClient,
    CpUnreachable,
)

# Module logger name used by BillingStateCache — caplog filters target this.
_CACHE_LOGGER = "shu.billing.billing_state_cache"

_T0 = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
_TTL = 60


def _state(disabled: bool = False) -> BillingState:
    return BillingState(
        openrouter_key_disabled=disabled,
        payment_failed_at=None,
        payment_grace_days=0,
    )


def _stub_client(
    *,
    return_values: list[BillingState] | None = None,
    side_effects: list[BaseException | BillingState] | None = None,
) -> MagicMock:
    """Stub CpClient where each call returns / raises the next item in turn."""
    client = MagicMock(spec=CpClient)
    if side_effects is not None:
        client.fetch_billing_state = AsyncMock(side_effect=side_effects)
    else:
        assert return_values is not None
        client.fetch_billing_state = AsyncMock(side_effect=return_values)
    return client


def _stepping_clock(start: datetime, step: timedelta) -> Callable[[], datetime]:
    """Returns a clock that advances by `step` on each call.

    Used to drive TTL boundaries deterministically without sleeps.
    """
    iterator: Iterator[datetime] = iter(
        start + step * i for i in range(10_000)
    )
    return lambda: next(iterator)


class _ManualClock:
    """Clock controlled explicitly by the test, decoupled from call count.

    Use this when the test needs to reason about TTL/backoff boundaries —
    `_stepping_clock` advances on every call and breaks when production
    code adds or removes a clock read.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _make_cache(
    client: MagicMock,
    *,
    ttl: int = _TTL,
    clock: Callable[[], datetime] | None = None,
) -> BillingStateCache:
    return BillingStateCache(
        client=client,
        ttl_seconds=ttl,
        clock=clock if clock is not None else lambda: _T0,
    )


@pytest.mark.asyncio
async def test_cold_start_success_caches_value() -> None:
    expected = _state(disabled=True)
    client = _stub_client(return_values=[expected])

    result = await _make_cache(client).get()

    assert result == expected
    client.fetch_billing_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_hit_makes_no_http_call() -> None:
    client = _stub_client(return_values=[_state()])
    cache = _make_cache(client)
    await cache.get()

    await cache.get()
    await cache.get()

    assert client.fetch_billing_state.await_count == 1


@pytest.mark.asyncio
async def test_expired_entry_triggers_one_fetch() -> None:
    first, second = _state(disabled=False), _state(disabled=True)
    client = _stub_client(return_values=[first, second])
    # Each clock tick advances by TTL+1, so the freshness check inside the
    # second get() observes age > TTL and triggers a fetch.
    cache = _make_cache(client, clock=_stepping_clock(_T0, timedelta(seconds=_TTL + 1)))

    assert await cache.get() == first
    assert await cache.get() == second
    assert client.fetch_billing_state.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_callers_coalesce_to_one_fetch() -> None:
    fetch_count = 0
    expected = _state(disabled=True)

    async def slow_fetch() -> BillingState:
        nonlocal fetch_count
        fetch_count += 1
        # Yield so the lock holder sleeps and the other 99 callers all queue
        # behind the lock — without the await the holder would finish before
        # any contention materialized.
        await asyncio.sleep(0)
        return expected

    client = MagicMock(spec=CpClient)
    client.fetch_billing_state = AsyncMock(side_effect=slow_fetch)
    cache = _make_cache(client)

    results = await asyncio.gather(*(cache.get() for _ in range(100)))

    assert all(r == expected for r in results)
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_cold_start_with_cp_failure_returns_healthy_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _stub_client(side_effects=[CpUnreachable("down")])

    with caplog.at_level(logging.WARNING, logger=_CACHE_LOGGER):
        result = await _make_cache(client).get()

    assert result == HEALTHY_DEFAULT
    assert any(
        "cold start" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_warm_cache_with_cp_failure_serves_stale_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seeded = _state(disabled=True)
    client = _stub_client(side_effects=[seeded, CpUnreachable("down")])
    cache = _make_cache(client, clock=_stepping_clock(_T0, timedelta(seconds=_TTL + 1)))

    await cache.get()  # cold-start success populates the cache.

    with caplog.at_level(logging.WARNING, logger=_CACHE_LOGGER):
        # Second call lands past TTL with CP failing.
        result = await cache.get()

    assert result == seeded
    assert any("stale" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_last_success_at_not_advanced_on_failure() -> None:
    """Failures must leave `_last_success_at` alone so the next retry happens
    on the next TTL boundary, not deferred a full extra cycle.

    Sequence: cold success at T0 → CP failure at T0+31 → CP success at T0+62.
    The third call must trigger a fetch (TTL elapsed since the LAST success).
    Without this invariant, the failure would have moved the retry baseline
    forward and the third call would still see "fresh" cache.
    """
    s1, s2 = _state(disabled=False), _state(disabled=True)
    client = _stub_client(side_effects=[s1, CpUnreachable("blip"), s2])
    cache = _make_cache(client, clock=_stepping_clock(_T0, timedelta(seconds=_TTL + 1)))

    assert await cache.get() == s1  # success @ T0
    assert await cache.get() == s1  # failure @ T0+31, serves stale
    assert await cache.get() == s2  # success @ T0+62, cache replaced

    assert client.fetch_billing_state.await_count == 3


@pytest.mark.asyncio
async def test_recovery_after_failure_replaces_stale_value() -> None:
    s1, s2 = _state(disabled=True), _state(disabled=False)
    client = _stub_client(side_effects=[s1, CpUnreachable("blip"), s2])
    cache = _make_cache(client, clock=_stepping_clock(_T0, timedelta(seconds=_TTL + 1)))

    await cache.get()
    await cache.get()
    recovered = await cache.get()

    assert recovered == s2


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [10, 120])
async def test_configured_ttl_governs_freshness_window(ttl: int) -> None:
    s1, s2 = _state(disabled=False), _state(disabled=True)
    client = _stub_client(return_values=[s1, s2])
    # Step > ttl so the freshness check inside the second get() expires.
    step = timedelta(seconds=ttl + 1)
    cache = _make_cache(client, ttl=ttl, clock=_stepping_clock(_T0, step))

    assert await cache.get() == s1
    assert await cache.get() == s2
    assert client.fetch_billing_state.await_count == 2


# Failure backoff: after a failed fetch attempt, the cache must NOT retry CP
# until one full TTL has elapsed since the failure. The naive implementation
# (without _next_retry_after) re-fetches on every call past the success-TTL,
# turning a CP outage into one serialized 5s timeout per consumer call.


@pytest.mark.asyncio
async def test_cold_start_failure_suppresses_retries_within_backoff_window() -> None:
    client = _stub_client(side_effects=[CpUnreachable("down"), CpUnreachable("down")])
    clock = _ManualClock(_T0)
    cache = _make_cache(client, clock=clock)

    assert await cache.get() == HEALTHY_DEFAULT  # @ T0: first attempt fails

    clock.advance(timedelta(seconds=_TTL // 2))  # well within backoff
    assert await cache.get() == HEALTHY_DEFAULT  # served without fetch

    assert client.fetch_billing_state.await_count == 1


@pytest.mark.asyncio
async def test_warm_cache_failure_suppresses_retries_within_backoff_window() -> None:
    seeded = _state(disabled=True)
    client = _stub_client(
        side_effects=[seeded, CpUnreachable("down"), CpUnreachable("still down")]
    )
    clock = _ManualClock(_T0)
    cache = _make_cache(client, clock=clock)

    assert await cache.get() == seeded  # cold-start success @ T0

    clock.advance(timedelta(seconds=_TTL + 1))  # past success-TTL
    assert await cache.get() == seeded  # one fetch, fails, serves stale

    clock.advance(timedelta(seconds=_TTL // 2))  # within failure backoff
    assert await cache.get() == seeded  # served stale, no fetch

    assert client.fetch_billing_state.await_count == 2


@pytest.mark.asyncio
async def test_failure_backoff_expires_one_ttl_after_the_failed_attempt() -> None:
    s1, s2 = _state(disabled=True), _state(disabled=False)
    client = _stub_client(side_effects=[s1, CpUnreachable("blip"), s2])
    clock = _ManualClock(_T0)
    cache = _make_cache(client, clock=clock)

    assert await cache.get() == s1  # success @ T0

    clock.advance(timedelta(seconds=_TTL + 1))  # past success-TTL
    assert await cache.get() == s1  # fetch fails, serves stale, arms backoff

    clock.advance(timedelta(seconds=_TTL + 1))  # past backoff window
    assert await cache.get() == s2  # CP recovered, cache replaced

    assert client.fetch_billing_state.await_count == 3


@pytest.mark.asyncio
async def test_cp_auth_failed_is_treated_identically_to_unreachable() -> None:
    """Spec task 10(i): CpAuthFailed must trigger stale-while-error fail-open
    just like CpUnreachable — CP returning 401 (operator misconfig) must not
    lock customers out.
    """
    seeded = _state(disabled=False)
    client = _stub_client(side_effects=[seeded, CpAuthFailed("bad sig")])
    clock = _ManualClock(_T0)
    cache = _make_cache(client, clock=clock)

    assert await cache.get() == seeded
    clock.advance(timedelta(seconds=_TTL + 1))
    assert await cache.get() == seeded  # served stale, not raised

    clock.advance(timedelta(seconds=_TTL // 2))
    assert await cache.get() == seeded  # within backoff, no fetch
    assert client.fetch_billing_state.await_count == 2
