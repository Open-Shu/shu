"""Unit tests for ``StreamLifecycle`` (SHU-802).

The lifecycle is the cross-task signal channel that ties chat SSE
streaming to disconnect / user-terminate / shutdown handling. Its
correctness rests on three invariants:

1. **Priority-based reason locking.** Intentional server-side stops
   (``user_terminated`` from the terminate endpoint, ``shutdown`` from
   the lifespan drain) outrank an incidental ``client_disconnected``
   and must override it regardless of firing order on the event loop.
   Same-priority signals (e.g. a second ``user_terminated``) stay
   first-writer-wins within a tier. The intent ordering matters:
   ``shutdown`` / ``user_terminated`` describe what the server decided
   to do; ``client_disconnected`` only describes what the client did.
   Without the override, AC12 fails — the shutdown drain would no-op
   on disconnected streams (their reason already set), the consumer
   loop wouldn't short-circuit (only ``user_terminated`` /
   ``shutdown`` trigger short-circuit), and the streams would race
   the drain timeout. The mis-stamped ``Message.message_metadata
   ["stream_state"]`` in the audit trail is the secondary symptom.

2. **The event always fires, even when the reason was already taken.**
   Coroutines blocked on ``event.wait()`` must still wake up; the only
   thing ``signal()`` skips on a no-op call is the *reason write*.

3. **``fire_on_complete()`` runs the registry-cleanup callback exactly
   once and never raises.** The supervisor invokes it from its
   ``finally`` block; if the callback raised it could break the
   supervisor's own cleanup. If it ran twice (e.g. supervisor + drain
   path both firing) the dict.pop would still be safe, but the
   callback semantics shouldn't depend on the caller's idempotency.

Per ``docs/policies/TESTING.md``, these are unit-test-only invariants:
integration tests can't reliably reproduce the concurrent terminate +
disconnect race, and the cost of the bug — silently mis-attributed
``stream_state`` in production audit data — is exactly the kind of
state-sync defect TESTING.md's gate is meant to catch.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from shu.services.chat_streaming import StreamLifecycle


def _build_lifecycle(**overrides) -> StreamLifecycle:
    """Construct a lifecycle with conservative test defaults.

    All three identifying fields default to fixed strings so the tests
    don't need to thread UUIDs through every fixture — these tests don't
    care what the identifiers are, only how ``signal`` / ``resolved_reason``
    / ``fire_on_complete`` behave under various transitions.
    """
    defaults = {
        "stream_id": "test-stream",
        "user_id": "test-user",
        "conversation_id": "test-conv",
    }
    defaults.update(overrides)
    return StreamLifecycle(**defaults)


class TestStreamLifecycleConstruction:
    """Default field values on a freshly-constructed lifecycle."""

    def test_defaults_to_unsignalled(self):
        lc = _build_lifecycle()
        assert lc.reason is None, "fresh lifecycle should have no reason set"
        assert not lc.event.is_set(), "fresh lifecycle's event should be unset"

    def test_defaults_have_no_callbacks(self):
        lc = _build_lifecycle()
        assert lc.on_complete is None
        assert lc.supervise_task is None

    def test_resolved_reason_defaults_to_complete(self):
        """The happy-path stream_state stamp — finalize reads this when
        the LLM completed naturally and nothing signalled the lifecycle."""
        lc = _build_lifecycle()
        assert lc.resolved_reason() == "complete"


class TestStreamLifecycleSignalAcceptance:
    """Priority-based ordering semantics for ``signal()``.

    Priority map (high → low):
        ``shutdown`` == ``user_terminated`` (intentional stops, tier 2)
        ``client_disconnected`` (incidental, tier 1)
        ``complete`` / unsignalled (tier 0)

    Higher tier overrides lower; same-tier signals are first-writer-wins.
    """

    def test_first_signal_is_accepted(self):
        lc = _build_lifecycle()
        accepted = lc.signal("client_disconnected")
        assert accepted is True
        assert lc.reason == "client_disconnected"
        assert lc.event.is_set()
        assert lc.resolved_reason() == "client_disconnected"

    def test_user_terminated_overrides_client_disconnected(self):
        """user_terminated fired after client_disconnected — intentional
        stop must outrank the incidental disconnect. Without this,
        a user who disconnected then clicked Stop from another tab
        would have their click ignored and the stream wouldn't
        short-circuit (the consumer loop's check excludes
        client_disconnected by design — see AC9 in SHU-802)."""
        lc = _build_lifecycle()
        lc.signal("client_disconnected")
        accepted = lc.signal("user_terminated")
        assert accepted is True, "intentional stop must override incidental disconnect"
        assert lc.reason == "user_terminated"
        assert lc.resolved_reason() == "user_terminated"

    def test_shutdown_overrides_client_disconnected(self):
        """The headline AC12 case: a client that disconnected mid-stream
        is still on the event loop running the LLM call when the
        process receives SIGTERM. The drain calls signal("shutdown");
        without the priority override the reason would stay
        client_disconnected, the consumer loop wouldn't short-circuit
        (it filters for user_terminated / shutdown only), and the
        stream would race the drain timeout. Priority override means
        the drain can deterministically signal shutdown and the
        variants can short-circuit with whatever partial content they
        have."""
        lc = _build_lifecycle()
        lc.signal("client_disconnected")
        accepted = lc.signal("shutdown")
        assert accepted is True, "shutdown must override client_disconnected per AC12"
        assert lc.reason == "shutdown"

    def test_client_disconnected_does_not_override_user_terminated(self):
        """The reverse direction: user_terminated already won, then
        client_disconnected arrives (e.g. user clicked Stop, then
        closed the tab while terminate was still processing). The
        intentional stop must stay locked in."""
        lc = _build_lifecycle()
        lc.signal("user_terminated")
        accepted = lc.signal("client_disconnected")
        assert accepted is False, "lower-priority signal must not override"
        assert lc.reason == "user_terminated"

    def test_client_disconnected_does_not_override_shutdown(self):
        lc = _build_lifecycle()
        lc.signal("shutdown")
        accepted = lc.signal("client_disconnected")
        assert accepted is False
        assert lc.reason == "shutdown"

    def test_shutdown_after_user_terminated_is_no_op(self):
        """Same-priority pair: first writer wins within the intentional
        tier. If the user explicitly clicked Stop and the process is
        then shutting down, the stream_state stamp stays user_terminated
        — that's a more informative audit record than shutdown."""
        lc = _build_lifecycle()
        lc.signal("user_terminated")
        accepted = lc.signal("shutdown")
        assert accepted is False, "same-priority signal must be first-writer-wins"
        assert lc.reason == "user_terminated"

    def test_user_terminated_after_shutdown_is_no_op(self):
        """Reverse same-priority pair: shutdown locked in first, a late
        user_terminated stays no-op."""
        lc = _build_lifecycle()
        lc.signal("shutdown")
        accepted = lc.signal("user_terminated")
        assert accepted is False
        assert lc.reason == "shutdown"

    def test_repeated_same_signal_is_no_op(self):
        """Signalling the same reason twice is a clean no-op on reason."""
        lc = _build_lifecycle()
        lc.signal("client_disconnected")
        accepted = lc.signal("client_disconnected")
        assert accepted is False
        assert lc.reason == "client_disconnected"

    def test_event_fires_even_on_no_op_signal(self):
        """A caller observing ``False`` from signal() still needs the
        event to wake them up — e.g. a stray client_disconnected after
        user_terminated landed should still trip any consumer waiting
        on event.is_set()."""
        lc = _build_lifecycle()
        lc.signal("user_terminated")
        # Manually clear the event to detect whether the second signal
        # re-sets it. (Real code never clears the event; this is a test
        # affordance to make the assertion observable.)
        lc.event.clear()
        accepted = lc.signal("client_disconnected")
        assert accepted is False, "lower-priority signal stays no-op on reason"
        assert lc.event.is_set(), "every signal call must fire the event"

    @pytest.mark.parametrize(
        "reason",
        ["client_disconnected", "user_terminated", "shutdown"],
    )
    def test_all_real_signals_are_acceptable_on_fresh_lifecycle(self, reason):
        """Three of the four Literal values are real signals (the fourth,
        ``complete``, is the resolved-default when nothing fired). Each
        must override the unsignalled state when called on a fresh
        lifecycle."""
        lc = _build_lifecycle()
        assert lc.signal(reason) is True
        assert lc.reason == reason

    def test_signal_complete_on_fresh_lifecycle_is_no_op(self):
        """``complete`` is priority-0, same as the unsignalled state. It's
        the resolved-default — never signalled in production code — so
        signal("complete") is intentionally a no-op rather than locking
        the lifecycle into ``complete`` and shadowing later real signals."""
        lc = _build_lifecycle()
        accepted = lc.signal("complete")
        assert accepted is False
        assert lc.reason is None, "complete-priority signal should not lock in a reason"
        # A real later signal still wins.
        assert lc.signal("client_disconnected") is True
        assert lc.reason == "client_disconnected"


class TestStreamLifecycleFireOnComplete:
    """``fire_on_complete`` invariants: once-only, exception-safe, optional."""

    def test_no_callback_is_safe(self):
        """Lifecycles constructed without an ``on_complete`` (e.g. the
        stub synthesized inside stream_ensemble_responses for tests that
        don't register in app.state) must tolerate fire_on_complete being
        called."""
        lc = _build_lifecycle()
        # Should not raise.
        lc.fire_on_complete()

    def test_callback_runs_once(self):
        """The drain path and the per-stream supervisor could both attempt
        to fire on_complete (e.g. supervisor completes during the drain's
        signal phase). The callback should run exactly once across all
        attempts so the registry pop doesn't get double-counted in metrics."""
        lc = _build_lifecycle()
        cb = MagicMock()
        lc.on_complete = cb

        lc.fire_on_complete()
        lc.fire_on_complete()  # second call

        cb.assert_called_once()

    def test_callback_exception_is_swallowed(self):
        """A bookkeeping failure (e.g. dict mutation racing a global teardown)
        must not bubble up out of fire_on_complete. The supervisor calls
        this in its own ``finally`` after gathering variant tasks; if the
        cleanup raised, the supervisor would surface a confusing error
        even though all the real work succeeded."""
        lc = _build_lifecycle()
        cb = MagicMock(side_effect=RuntimeError("registry exploded"))
        lc.on_complete = cb

        # Should not raise.
        lc.fire_on_complete()
        cb.assert_called_once()

    def test_after_fire_callback_is_cleared(self):
        """Once fired, the lifecycle drops its reference to the callback —
        so even if some other path resurrects the lifecycle (it shouldn't,
        but defensive) the callback can't be invoked a second time."""
        lc = _build_lifecycle()
        cb = MagicMock()
        lc.on_complete = cb
        lc.fire_on_complete()
        assert lc.on_complete is None


class TestStreamLifecycleEventWaitInterop:
    """The event is meant to be observed via ``await event.wait()`` in
    coroutines that need to short-circuit on signal. These tests pin the
    wait/wake semantics under signal so a refactor of ``signal()`` can't
    accidentally drop the wakeup behavior.
    """

    @pytest.mark.asyncio
    async def test_waiter_wakes_after_signal(self):
        """A coroutine awaiting event.wait() wakes when signal() fires."""
        lc = _build_lifecycle()

        async def waiter():
            await lc.event.wait()
            return lc.resolved_reason()

        task = asyncio.create_task(waiter())
        # Yield so the waiter actually parks on event.wait() before we signal.
        await asyncio.sleep(0)
        lc.signal("user_terminated")
        # asyncio.wait_for adds a small budget so a regression that
        # accidentally stops setting the event surfaces as a clean
        # TimeoutError rather than a hanging test.
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == "user_terminated"

    @pytest.mark.asyncio
    async def test_event_is_already_set_after_signal(self):
        """A coroutine that calls event.wait() *after* signal already fired
        returns immediately — the event stays set, it's not edge-triggered."""
        lc = _build_lifecycle()
        lc.signal("shutdown")
        # event.wait() on an already-set event returns immediately.
        await asyncio.wait_for(lc.event.wait(), timeout=0.1)
        assert lc.resolved_reason() == "shutdown"
