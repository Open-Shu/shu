"""Unit tests for ``StreamLifecycle`` (SHU-802).

The lifecycle is the cross-task signal channel that ties chat SSE
streaming to disconnect / user-terminate / shutdown handling. Its
correctness rests on three invariants:

1. **First-writer-wins reason locking.** If a server-initiated stop
   (``user_terminated`` from the terminate endpoint, ``shutdown`` from
   the lifespan drain) and a client disconnect race each other on the
   event loop, the *first* signal must win. The intent ordering is
   intentional: ``shutdown`` and ``user_terminated`` describe what the
   server decided to do, ``client_disconnected`` only describes what
   the client did. Without the lock, a stray ``client_disconnected``
   signal arriving after a ``user_terminated`` would mis-stamp
   ``Message.message_metadata["stream_state"]`` and obscure why the
   stream ended in the audit trail.

2. **The event always fires, even when the reason was already taken.**
   Coroutines blocked on ``event.wait()`` must still wake up; the only
   thing ``signal()`` skips on a second call is the *reason write*.

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
    """First-writer-wins semantics for ``signal()``."""

    def test_first_signal_is_accepted(self):
        lc = _build_lifecycle()
        accepted = lc.signal("client_disconnected")
        assert accepted is True
        assert lc.reason == "client_disconnected"
        assert lc.event.is_set()
        assert lc.resolved_reason() == "client_disconnected"

    def test_second_signal_is_no_op_on_reason(self):
        """user_terminated arrives after client_disconnected already won —
        reason stays client_disconnected (first writer). This is the
        less-intuitive direction; the priority-feel ordering (user_terminated
        > client_disconnected) is *not* enforced by reason precedence — it's
        enforced by *which signal fires first* in practice (the terminate
        endpoint fires synchronously from the request handler, the
        client_disconnected only fires after the SSE wrapper's close hook,
        which runs later)."""
        lc = _build_lifecycle()
        lc.signal("client_disconnected")
        accepted = lc.signal("user_terminated")
        assert accepted is False, "second signal should report unaccepted"
        assert lc.reason == "client_disconnected", "first writer wins"

    def test_second_signal_still_fires_event(self):
        """A caller observing ``False`` from signal() still needs the
        event to wake them up — e.g. shutdown drain calling signal()
        on a stream that already disconnected should still trip the
        consumer loop's ``event.is_set()`` check on its next iteration."""
        lc = _build_lifecycle()
        lc.signal("client_disconnected")
        # Manually clear the event to detect whether the second signal
        # re-sets it. (Real code never clears the event; this is a test
        # affordance to make the assertion observable.)
        lc.event.clear()
        lc.signal("user_terminated")
        assert lc.event.is_set(), "every signal call must fire the event"

    def test_user_terminated_wins_when_it_fires_first(self):
        """The intent-ordering case the priority comment refers to —
        when user_terminated wins the race (fires first), it stays the
        reason and any later client_disconnected is shadowed."""
        lc = _build_lifecycle()
        lc.signal("user_terminated")
        lc.signal("client_disconnected")
        assert lc.reason == "user_terminated"
        assert lc.resolved_reason() == "user_terminated"

    def test_shutdown_wins_when_it_fires_first(self):
        lc = _build_lifecycle()
        lc.signal("shutdown")
        lc.signal("client_disconnected")
        assert lc.reason == "shutdown"

    @pytest.mark.parametrize(
        "reason",
        ["complete", "client_disconnected", "user_terminated", "shutdown"],
    )
    def test_all_valid_reasons_are_acceptable(self, reason):
        """The Literal type allows four values; each must work as the first
        signal. ``complete`` is unusual to signal explicitly (it's the default
        when nothing fires) but the API has to accept it — finalize-success
        paths could theoretically signal it for symmetry."""
        lc = _build_lifecycle()
        assert lc.signal(reason) is True
        assert lc.reason == reason


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
