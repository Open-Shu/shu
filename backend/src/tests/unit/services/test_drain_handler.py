"""Unit tests for ``drain_in_flight_streams`` (SHU-802).

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

from shu.services.chat_streaming import (
    StreamLifecycle,
    drain_in_flight_streams,
    signal_shutdown_to_in_flight_streams,
)


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


class TestSignalShutdownToInFlightStreams:
    """SHU-802: the sync signal-handler helper invoked from ``main.py``'s
    SIGTERM/SIGINT handler. Preempts uvicorn's graceful-shutdown wait by
    firing the shutdown signal on every registered lifecycle before
    lifespan.shutdown gets a chance to run. Separate from the async
    ``drain_in_flight_streams`` because signal handlers must be sync —
    ``StreamLifecycle.signal()`` is sync, so this helper just iterates.
    """

    def test_empty_registry_returns_zero(self):
        """No in-flight streams → handler is a no-op returning 0. Guards
        against a misordered iteration that would crash on a missing
        ``.values()`` or similar Python foot-gun."""
        result = signal_shutdown_to_in_flight_streams({})
        assert result == 0

    def test_signals_every_lifecycle_with_shutdown(self):
        """Every lifecycle in the registry receives ``signal('shutdown')``.
        Each one's ``reason`` is set to ``shutdown`` (priority semantics
        from the earlier signal() fix mean nothing was blocking this — a
        fresh lifecycle has priority 0 and shutdown is priority 2).
        """
        registry: dict[str, StreamLifecycle] = {}
        for i in range(3):
            lc = _build_lifecycle(f"stream-{i}")
            registry[lc.stream_id] = lc

        count = signal_shutdown_to_in_flight_streams(registry)

        assert count == 3
        for lc in registry.values():
            assert lc.reason == "shutdown", (
                f"lifecycle {lc.stream_id} should be signalled with reason=shutdown; got {lc.reason!r}"
            )
            assert lc.event.is_set(), (
                f"lifecycle {lc.stream_id} event should be set after signal"
            )

    def test_overrides_existing_client_disconnected_reason(self):
        """The load-bearing case the SHU-802 priority-signal fix enabled:
        a stream that fired ``client_disconnected`` first (e.g. the user
        closed the tab) must still get its reason overridden to
        ``shutdown`` when the SIGTERM handler runs. Without this, the
        consumer loop's short-circuit check (which excludes
        ``client_disconnected``) would keep the LLM running until SIGKILL.
        """
        lc = _build_lifecycle("disconnected-first")
        lc.signal("client_disconnected")
        assert lc.reason == "client_disconnected"
        registry = {lc.stream_id: lc}

        count = signal_shutdown_to_in_flight_streams(registry)

        assert count == 1
        assert lc.reason == "shutdown", (
            "shutdown must override the lower-priority client_disconnected"
        )

    def test_snapshot_guards_against_mid_iteration_mutation(self):
        """A supervisor completing mid-iteration would call its
        ``on_complete`` callback, which pops the registry entry — if the
        helper iterated ``registry.values()`` directly that would raise
        ``RuntimeError: dictionary changed size during iteration``. The
        helper takes a ``list(registry.values())`` snapshot before
        iterating so concurrent mutations don't crash the handler AND
        every original lifecycle still receives the signal.
        """
        registry: dict[str, StreamLifecycle] = {}
        first = _build_lifecycle("first")
        second = _build_lifecycle("second")
        registry[first.stream_id] = first
        registry[second.stream_id] = second

        # Wrap first.signal so calling it triggers the registry mutation
        # MID-iteration (the realistic case: a supervisor on a parallel
        # task fires its on_complete callback, popping another lifecycle's
        # entry from app.state.in_flight_streams while the signal handler
        # is still iterating). Without the helper's
        # `list(registry.values())` snapshot, this would raise
        # ``RuntimeError: dictionary changed size during iteration`` as
        # soon as the next iteration step looked at the dict.
        original_signal = first.signal

        def mutating_signal(reason):
            registry.pop(second.stream_id, None)
            return original_signal(reason)

        first.signal = mutating_signal  # type: ignore[method-assign]

        # Must not raise. If the snapshot guard regresses, this call
        # raises RuntimeError mid-iteration.
        count = signal_shutdown_to_in_flight_streams(registry)

        # Both lifecycles received the signal — even `second`, which was
        # popped from the registry during the iteration. Proof that the
        # snapshot was taken before mutation and that every original
        # entry was processed.
        assert first.reason == "shutdown"
        assert second.reason == "shutdown", (
            "second must be signalled even though it was popped from the "
            "registry mid-iteration — the snapshot taken at the start of "
            "the helper protects against concurrent mutation"
        )
        # Count reflects the snapshot length (2), not the post-mutation
        # registry length (1).
        assert count == 2


# SHU-803: both shutdown-signal helpers must set
# ``lifecycle.shutdown_signaled = True`` IN ADDITION to calling
# ``lc.signal("shutdown")``. The separate flag is the SIGTERM escape
# valve for the per-stream drain loop in ``_call_provider``, which
# can't rely on ``reason`` alone (priority semantics block
# ``shutdown`` from overriding an existing ``user_terminated``).
# Without this flag, an in-flight drain following user-terminate
# would have no way to detect SIGTERM and would only exit via
# cancellation propagation — bypassing the shielded finalize.


class TestShutdownSignaledFlag:
    """SHU-803: shutdown_signaled is the drain-escape-valve flag."""

    def test_signal_shutdown_helper_sets_flag_on_every_lifecycle(self):
        """``signal_shutdown_to_in_flight_streams`` is what the asyncio
        SIGTERM signal handler calls (sync path). Every lifecycle in the
        registry gets BOTH ``shutdown_signaled = True`` AND a
        ``signal("shutdown")`` call. The flag is what the drain loop
        observes between events.
        """
        first = _build_lifecycle("stream-1")
        second = _build_lifecycle("stream-2")
        registry = {first.stream_id: first, second.stream_id: second}

        assert first.shutdown_signaled is False
        assert second.shutdown_signaled is False

        signal_shutdown_to_in_flight_streams(registry)

        assert first.shutdown_signaled is True
        assert second.shutdown_signaled is True
        # And the reason update still happened.
        assert first.reason == "shutdown"
        assert second.reason == "shutdown"

    @pytest.mark.asyncio
    async def test_drain_helper_sets_flag_on_every_lifecycle(self):
        """``drain_in_flight_streams`` (the lifespan backstop) also sets
        the flag so a lifespan-only path — running without the SIGTERM
        signal handler having fired first, e.g. on Windows dev without
        ``add_signal_handler`` — still trips the drain escape valve.
        """
        first = _build_lifecycle("stream-1")
        second = _build_lifecycle("stream-2")
        # Give them no-op supervisor tasks so drain has something to
        # await on.
        first.supervise_task = asyncio.create_task(asyncio.sleep(0))
        second.supervise_task = asyncio.create_task(asyncio.sleep(0))
        registry = {first.stream_id: first, second.stream_id: second}

        assert first.shutdown_signaled is False
        assert second.shutdown_signaled is False

        await drain_in_flight_streams(registry, timeout_seconds=1.0)

        assert first.shutdown_signaled is True
        assert second.shutdown_signaled is True

    def test_flag_survives_user_terminated_first_writer_wins(self):
        """The case the flag exists for: ``user_terminated`` won the
        ``reason`` slot at priority 2, then SIGTERM fires shutdown. The
        priority rule keeps ``reason="user_terminated"`` (audit trail
        intact), but ``shutdown_signaled`` flips to True so the drain
        knows to exit.
        """
        lc = _build_lifecycle("stream-1")
        lc.signal("user_terminated")
        assert lc.reason == "user_terminated"
        assert lc.shutdown_signaled is False

        registry = {lc.stream_id: lc}
        signal_shutdown_to_in_flight_streams(registry)

        # ``reason`` stays user_terminated (first-writer-wins at tier 2).
        assert lc.reason == "user_terminated"
        # But the flag is set — drain detects it and exits gracefully.
        assert lc.shutdown_signaled is True
