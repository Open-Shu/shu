"""Unit tests for ``drain_in_flight_streams`` (SHU-784).

The drain is the lifespan-shutdown counterpart to the per-stream
supervisor: when the process receives SIGTERM, every entry in
``app.state.in_flight_streams`` gets its ``shutdown`` signal fired and
the drain awaits the supervisor tasks until either they all complete or
the configured timeout expires.

These tests exist because the timeout-vs-cancel control flow is
notoriously easy to get subtly wrong, and integration tests can't
reliably reproduce a stuck supervisor (you'd need an actual hung LLM
provider or sleep-injected variant) — exactly the "will this catch
a bug integration tests won't?" gate from ``docs/policies/TESTING.md``.

Three concrete scenarios are pinned here:

1. **All-fast**: every supervisor completes well within the budget.
   Drain returns 0 (clean shutdown).
2. **Mixed fast/slow/stuck**: fast and slow supervisors finish; the
   stuck one is still pending when the timeout fires. Drain returns
   the stuck count.
3. **All-signalled, no supervisors registered yet**: a stream that
   registered its lifecycle but crashed before assigning a
   supervise_task. Drain still signals every lifecycle and returns 0.

Note: a real production stuck supervisor would be holding a DB session
for its shielded finalize; this test fakes the supervisor as a plain
``asyncio.sleep`` so the drain logic is exercised without dragging in
the DB pool.
"""

from __future__ import annotations

import asyncio

import pytest

from shu.services.chat_streaming import StreamLifecycle, drain_in_flight_streams


def _build_lifecycle(stream_id: str = "test-stream") -> StreamLifecycle:
    return StreamLifecycle(
        stream_id=stream_id,
        user_id="test-user",
        conversation_id="test-conv",
    )


async def _supervisor_completes_after(delay: float) -> None:
    """Fake supervisor body: just sleep ``delay`` then return."""
    await asyncio.sleep(delay)


@pytest.mark.asyncio
class TestDrainInFlightStreams:
    """Behavior of the lifespan shutdown drain in isolation."""

    async def test_empty_registry_returns_zero(self):
        """No in-flight streams → drain is a no-op returning 0. Catches
        accidental signal/gather calls on an empty list that could
        regress to AttributeError or other Python footguns."""
        result = await drain_in_flight_streams({}, timeout_seconds=1.0)
        assert result == 0

    async def test_all_fast_supervisors_drain_cleanly(self):
        """Every supervisor finishes well within budget — drain returns 0.
        Also verifies every lifecycle's `signal("shutdown")` fired so
        consumer loops in the variant tasks would observe the stop.
        """
        registry: dict[str, StreamLifecycle] = {}
        for i in range(3):
            lc = _build_lifecycle(f"stream-{i}")
            lc.supervise_task = asyncio.create_task(_supervisor_completes_after(0.01))
            registry[lc.stream_id] = lc

        result = await drain_in_flight_streams(registry, timeout_seconds=1.0)

        assert result == 0
        for lc in registry.values():
            assert lc.reason == "shutdown", (
                f"lifecycle {lc.stream_id} reason should be 'shutdown' after drain"
            )
            assert lc.supervise_task is not None
            assert lc.supervise_task.done(), (
                f"lifecycle {lc.stream_id} supervisor should be done"
            )

    async def test_stuck_supervisor_is_counted_and_cancelled(self):
        """Fast and slow supervisors complete; stuck one outlasts the
        budget. Drain returns 1 (the stuck one) and asyncio.wait_for
        cancels the still-pending tasks as it unwinds — verifying both
        the return value (for the caller's loud ERROR log) and the
        cancellation side-effect (so the process can actually exit).
        """
        registry: dict[str, StreamLifecycle] = {}

        fast = _build_lifecycle("fast")
        fast.supervise_task = asyncio.create_task(_supervisor_completes_after(0.01))
        registry[fast.stream_id] = fast

        slow = _build_lifecycle("slow")
        slow.supervise_task = asyncio.create_task(_supervisor_completes_after(0.05))
        registry[slow.stream_id] = slow

        stuck = _build_lifecycle("stuck")
        # A sleep longer than any conceivable drain budget — wait_for
        # has to cancel this.
        stuck.supervise_task = asyncio.create_task(_supervisor_completes_after(60.0))
        registry[stuck.stream_id] = stuck

        result = await drain_in_flight_streams(registry, timeout_seconds=0.2)

        assert result == 1, (
            f"expected 1 stuck supervisor counted as killed, got {result}"
        )
        assert fast.supervise_task.done()
        assert slow.supervise_task.done()
        # The stuck task is cancelled by wait_for's unwind, but the
        # cancellation may not be observable until one more event-loop
        # turn. Yield once so the cancellation propagates, then assert.
        await asyncio.sleep(0)
        assert stuck.supervise_task.cancelled() or stuck.supervise_task.done(), (
            "stuck supervisor should be cancelled or completed after drain"
        )

    async def test_signal_fires_before_wait(self):
        """All lifecycles receive `signal("shutdown")` BEFORE the drain
        starts waiting on the supervise_tasks. The order matters: in
        production, the consumer loop's `event.is_set()` check has to
        observe the signal so it can short-circuit and let the
        supervisor finish — if we waited first then signalled, the
        consumer loop would still be awaiting the next provider event
        when the drain timeout fired.

        Verified here by using a supervisor that explicitly observes
        the signal on its way to completion: if the signal hadn't
        fired before wait_for, the supervisor would block forever on
        `lifecycle.event.wait()`.
        """
        lc = _build_lifecycle("waiter")

        async def supervisor_that_awaits_shutdown_signal() -> None:
            # Mimics what the real consumer loop does — wait for the
            # event to fire, then finish.
            await lc.event.wait()
            assert lc.reason == "shutdown", "signal must have set reason before event"

        lc.supervise_task = asyncio.create_task(supervisor_that_awaits_shutdown_signal())
        registry = {lc.stream_id: lc}

        result = await drain_in_flight_streams(registry, timeout_seconds=1.0)

        assert result == 0
        assert lc.supervise_task.done() and not lc.supervise_task.cancelled()

    async def test_lifecycles_without_supervisor_are_signalled_but_not_waited(self):
        """A lifecycle could be registered before its supervise_task is
        spawned (a tiny window in stream_ensemble_responses between
        registration and supervisor creation). The drain must:

        - Still call signal('shutdown') on those lifecycles (so any
          consumer that *does* spin up after the drain starts has the
          signal already set on its first check).
        - Not pass `None` into asyncio.gather (would TypeError).

        Returns 0 because there's nothing to actually wait for.
        """
        lc_with = _build_lifecycle("with-supervisor")
        lc_with.supervise_task = asyncio.create_task(_supervisor_completes_after(0.01))

        lc_without = _build_lifecycle("without-supervisor")
        # supervise_task left at None

        registry = {lc_with.stream_id: lc_with, lc_without.stream_id: lc_without}

        result = await drain_in_flight_streams(registry, timeout_seconds=1.0)

        assert result == 0
        assert lc_with.reason == "shutdown"
        assert lc_without.reason == "shutdown", (
            "lifecycle without supervisor must still receive the shutdown signal"
        )

    async def test_supervisor_raised_exception_still_counts_as_done(self):
        """A supervisor that crashed (e.g. its own `fire_on_complete`
        raised) is technically "done" — asyncio.gather with
        return_exceptions=True swallows the error. The drain should
        not report it as a kill because the variant tasks underneath
        the supervisor presumably already finished and the
        bookkeeping failure is a separate concern.
        """
        lc = _build_lifecycle("crasher")

        async def crashing_supervisor() -> None:
            raise RuntimeError("simulated supervisor bookkeeping failure")

        lc.supervise_task = asyncio.create_task(crashing_supervisor())
        registry = {lc.stream_id: lc}

        result = await drain_in_flight_streams(registry, timeout_seconds=1.0)

        assert result == 0, "exceptions are not timeouts; should not count as killed"
        assert lc.supervise_task.done()
