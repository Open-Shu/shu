"""Smoke tests for the PoolObserver helper (SHU-759).

Verifies SQLAlchemy pool event listeners fire correctly under the custom
async integration framework's single-event-loop model. The PoolObserver is
the foundation for the AC#1 / AC#3 / AC#7 assertions later in SHU-759, so
proving the listeners attach and fire here de-risks those tests before the
production refactor begins.
"""

import asyncio
import logging
import sys
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.pool_observer import PoolObserver
from shu.core.database import get_async_engine, get_async_session_local

logger = logging.getLogger(__name__)


async def test_pool_observer_counts_query_checkout(client, db, auth_headers):
    """A fresh session running a query bumps max_in_window above the baseline."""
    engine = get_async_engine()
    session_factory = get_async_session_local()

    with PoolObserver(engine) as observer:
        observer.open_window()
        baseline = observer.current_checkouts

        # Open a fresh session (separate from `db`) so a new checkout fires.
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))

        stats = observer.close_window()

    assert stats.max_in_window > baseline, (
        f"Expected a checkout above baseline ({baseline}); got {stats.max_in_window}"
    )
    logger.info(
        "PoolObserver observed max_in_window=%d (baseline=%d) during single-query window",
        stats.max_in_window,
        baseline,
    )


async def test_pool_observer_zero_when_no_db_activity(client, db, auth_headers):
    """A window with no DB activity ends at the same level it opened."""
    engine = get_async_engine()

    with PoolObserver(engine) as observer:
        observer.open_window()
        baseline = observer.current_checkouts
        # No DB activity inside the window
        stats = observer.close_window()

    assert stats.max_in_window == baseline, (
        f"Expected max_in_window == baseline ({baseline}); got {stats.max_in_window}"
    )
    assert stats.cumulative_hold_seconds == 0.0, (
        f"Expected zero cumulative hold time on idle window; got {stats.cumulative_hold_seconds}"
    )


async def test_pool_observer_cumulative_hold_reflects_session_duration(client, db, auth_headers):
    """Holding a session ~100ms registers ~0.1s of cumulative hold time."""
    engine = get_async_engine()
    session_factory = get_async_session_local()

    with PoolObserver(engine) as observer:
        observer.open_window()
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            # Hold the session open for a known duration
            await asyncio.sleep(0.1)
        stats = observer.close_window()

    # Allow generous bounds — wall-clock sleeps are noisy under load
    assert 0.08 <= stats.cumulative_hold_seconds <= 0.2, (
        f"Expected cumulative_hold_seconds ~0.1s for 100ms held session; "
        f"got {stats.cumulative_hold_seconds:.4f}s"
    )
    assert stats.window_duration_seconds >= 0.1, (
        f"Expected window_duration ~0.1s; got {stats.window_duration_seconds:.4f}s"
    )
    logger.info(
        "Held 1 session ~100ms: cumulative=%.3fs, window=%.3fs, avg=%.2f",
        stats.cumulative_hold_seconds,
        stats.window_duration_seconds,
        stats.average_concurrent_checkouts,
    )


async def test_pool_observer_idempotent_start_stop(client, db, auth_headers):
    """Calling start()/stop() multiple times is safe and doesn't raise."""
    engine = get_async_engine()
    observer = PoolObserver(engine)
    observer.start()
    observer.start()  # idempotent
    observer.stop()
    observer.stop()  # idempotent


async def test_pool_observer_context_manager_releases_listeners(client, db, auth_headers):
    """After exiting the context manager, listeners are detached.

    Verified indirectly: a query run after exit must not increment the
    observer's counter. We assert by checking the observer's counter
    remains at the post-exit value despite further DB activity.
    """
    engine = get_async_engine()
    session_factory = get_async_session_local()

    with PoolObserver(engine) as observer:
        observer.open_window()
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        observer.close_window()

    counter_after_exit = observer.current_checkouts

    # Drive more activity outside the context — counter must not change
    async with session_factory() as session:
        await session.execute(text("SELECT 2"))

    assert observer.current_checkouts == counter_after_exit, (
        f"Counter changed after context exit: was {counter_after_exit}, "
        f"now {observer.current_checkouts} — listeners did not detach"
    )


class PoolObserverTestSuite(BaseIntegrationTestSuite):
    """Smoke test suite for the PoolObserver helper (SHU-759)."""

    def get_test_functions(self) -> list[Callable]:
        return [
            test_pool_observer_counts_query_checkout,
            test_pool_observer_zero_when_no_db_activity,
            test_pool_observer_cumulative_hold_reflects_session_duration,
            test_pool_observer_idempotent_start_stop,
            test_pool_observer_context_manager_releases_listeners,
        ]

    def get_suite_name(self) -> str:
        return "Pool Observer Helper Smoke Tests"

    def get_suite_description(self) -> str:
        return "Verify SQLAlchemy pool event listeners work under the custom async framework"


if __name__ == "__main__":
    suite = PoolObserverTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
