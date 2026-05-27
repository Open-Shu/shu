"""Unit tests for ``_iter_with_signal_break`` (SHU-803 follow-up).

The wrapper races each provider next-chunk read against
``lifecycle.event.wait()`` so the consumer-loop's terminate-detection
fires at signal time, not at next-chunk time. Without this guarantee,
a provider going silent after the user clicks Stop delays the
early-persist callback — and a refetch or follow-up message in the
gap reintroduces the vanishing-content + temporal-ordering races
SHU-803's early-persist guarantee is meant to prevent.

The integration-level coverage exists in the SHU-803 force-terminate
real-usage suite, but those tests run against stub providers that
stream chunks rapidly — they don't exercise the silent-provider gap
this wrapper closes. This file does, by feeding the wrapper an
explicit async generator that pauses on demand and asserting on the
sentinel-yield timing.

Three concrete scenarios are pinned here:

1. **Hot path** — chunks arrive faster than the signal fires. Wrapper
   yields each chunk normally; the sentinel never appears.
2. **Silent provider gap** — wrapper yields the sentinel within the
   asyncio scheduling latency of when the signal fires, NOT when the
   provider next yields. After the sentinel, the wrapper resumes
   yielding chunks (so drain can consume them).
3. **Cleanup on early exit** — consumer abandons the generator
   mid-iteration; pending next-chunk and signal-wait tasks are
   cancelled and their exceptions absorbed (no "Task exception was
   never retrieved" warnings).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from shu.services.chat_streaming import _SIGNAL_SENTINEL, _iter_with_signal_break


class _ControllableStream:
    """Async-iterable wrapper around an ``asyncio.Queue`` so tests can
    push chunks and a sentinel end-marker on demand. Mirrors the shape
    of the provider stream the real consumer loop iterates.
    """

    _END = object()  # in-band end-of-stream marker

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    def push(self, item: object) -> None:
        self._queue.put_nowait(item)

    def end(self) -> None:
        self._queue.put_nowait(self._END)

    def __aiter__(self) -> AsyncGenerator[object, None]:
        async def _gen() -> AsyncGenerator[object, None]:
            while True:
                item = await self._queue.get()
                if item is self._END:
                    return
                yield item

        return _gen()


@pytest.mark.asyncio
class TestIterWithSignalBreak:
    """Behavior of the signal-aware iterator wrapper in isolation."""

    async def test_hot_path_yields_chunks_without_sentinel(self):
        """When chunks arrive before any signal fires, the wrapper is
        transparent — yields each chunk and nothing else. Catches a
        regression where the sentinel-yield branch fires when it
        shouldn't.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()

        stream.push("chunk-1")
        stream.push("chunk-2")
        stream.push("chunk-3")
        stream.end()

        collected = [item async for item in _iter_with_signal_break(stream, signal_event)]
        assert collected == ["chunk-1", "chunk-2", "chunk-3"]
        assert _SIGNAL_SENTINEL not in collected

    async def test_sentinel_fires_when_signal_set_before_first_chunk(self):
        """Signal fires while the wrapper is awaiting the first chunk
        from a silent provider. The wrapper must yield the sentinel
        without waiting for a chunk that may never arrive — that's
        the load-bearing property this wrapper exists for.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()

        # Spawn the iteration; consumer collects via a queue so we can
        # observe yields in real time.
        collected: list[object] = []

        async def _consume() -> None:
            async for item in _iter_with_signal_break(stream, signal_event):
                collected.append(item)
                if item is _SIGNAL_SENTINEL:
                    # Stop consuming after the sentinel so the test
                    # doesn't hang on the never-arriving next chunk.
                    break

        task = asyncio.create_task(_consume())

        # Let the wrapper enter its wait state.
        await asyncio.sleep(0.01)
        # Provider is silent; fire the signal.
        signal_event.set()

        await asyncio.wait_for(task, timeout=1.0)
        assert collected == [_SIGNAL_SENTINEL]

    async def test_sentinel_then_chunks_resume_for_drain(self):
        """After the sentinel, the wrapper continues yielding chunks
        from the underlying stream — that's how the consumer's drain
        path captures end-of-stream usage post-terminate.

        Sequence: provider yields chunk-A; signal fires (sentinel);
        provider yields chunk-B + chunk-C + end. Wrapper output:
        chunk-A, _SIGNAL_SENTINEL, chunk-B, chunk-C.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()

        collected: list[object] = []

        async def _consume() -> None:
            async for item in _iter_with_signal_break(stream, signal_event):
                collected.append(item)

        task = asyncio.create_task(_consume())

        # Push first chunk + let it be observed.
        stream.push("chunk-A")
        await asyncio.sleep(0.01)

        # Fire signal while wrapper is awaiting next chunk.
        signal_event.set()
        await asyncio.sleep(0.01)

        # Resume the stream with more chunks (drain phase).
        stream.push("chunk-B")
        stream.push("chunk-C")
        stream.end()

        await asyncio.wait_for(task, timeout=1.0)
        assert collected == ["chunk-A", _SIGNAL_SENTINEL, "chunk-B", "chunk-C"]
        # Exactly one sentinel — never two.
        assert collected.count(_SIGNAL_SENTINEL) == 1

    async def test_signal_already_set_at_iteration_start_yields_sentinel_first(self):
        """Signal is already set when the wrapper's first iteration
        starts (terminate POST landed in the brief window between
        handleStreamingResponse starting and the wrapper entering its
        loop — e.g., user clicked Stop immediately after stream_start
        but before the provider's first content chunk).

        Pre-fix the wrapper assumed the caller's
        ``lifecycle.event.is_set()`` check would handle this — but the
        caller's check only runs AFTER the wrapper yields, and the
        wrapper was blocking on ``await pending_chunk``. That left the
        early-persist callback gated on the provider's next chunk
        arrival, which is exactly the silent-provider gap the wrapper
        exists to close.

        Post-fix: the wrapper yields the sentinel immediately when
        signal_event is set at iteration start, then transitions into
        normal chunk-yielding so drain captures whatever the provider
        eventually emits.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()
        signal_event.set()  # already set BEFORE iteration begins

        stream.push("chunk-1")
        stream.push("chunk-2")
        stream.end()

        collected = [item async for item in _iter_with_signal_break(stream, signal_event)]
        # Sentinel must be the FIRST yield — proves the wrapper didn't
        # wait for the provider's first chunk before observing the
        # signal.
        assert collected[0] is _SIGNAL_SENTINEL
        # Chunks resume after the sentinel for drain to consume.
        assert collected[1:] == ["chunk-1", "chunk-2"]
        # Sentinel yielded exactly once across the whole iteration.
        assert collected.count(_SIGNAL_SENTINEL) == 1

    async def test_signal_set_before_iteration_with_silent_provider_yields_sentinel(self):
        """The load-bearing variant of the test above: signal is
        already set AND the provider stays silent (never emits a
        chunk). Pre-fix the wrapper would block on ``await
        pending_chunk`` forever — early-persist never fires.

        Post-fix: the wrapper yields the sentinel immediately, the
        caller's terminate path runs (early-persist fires), and the
        consumer can break out of iteration cleanly once it observes
        the sentinel without waiting for a chunk that may never come.
        """
        stream = _ControllableStream()  # never pushed; never ended
        signal_event = asyncio.Event()
        signal_event.set()

        collected: list[object] = []

        async def _consume() -> None:
            async for item in _iter_with_signal_break(stream, signal_event):
                collected.append(item)
                if item is _SIGNAL_SENTINEL:
                    break

        # Tight deadline — if this exceeds the timeout, the wrapper
        # is blocked on a chunk read it should never have entered.
        await asyncio.wait_for(_consume(), timeout=1.0)
        assert collected == [_SIGNAL_SENTINEL]

    async def test_consumer_early_break_cancels_pending_tasks(self):
        """Consumer abandons the iterator mid-stream (e.g., drain
        catches ``ProviderFinalEventResult`` and breaks). The wrapper's
        ``finally`` block must cancel and absorb the dangling
        ``next-chunk`` and ``signal-wait`` tasks — otherwise asyncio
        emits ``Task exception was never retrieved`` warnings and the
        underlying HTTP read leaks.

        We can't directly observe task cancellation from the outside,
        but we CAN observe that no unhandled warnings emit and the
        generator's ``aclose()`` returns cleanly within a short
        deadline.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()

        gen = _iter_with_signal_break(stream, signal_event)

        # Consume one chunk, then break out and close the generator.
        stream.push("chunk-1")
        first = await gen.__anext__()
        assert first == "chunk-1"

        # Close the generator — this triggers the finally cleanup.
        # The next-chunk task is pending against an empty queue; the
        # signal-wait task is pending against an unset event. Both
        # must be cancelled and absorbed.
        await asyncio.wait_for(gen.aclose(), timeout=1.0)

    async def test_only_one_sentinel_emitted_across_long_drain(self):
        """A drain that runs over many chunks must not re-yield the
        sentinel for each chunk. The wrapper sets ``signal_yielded``
        on the first sentinel and skips the race thereafter, so even
        a long drain with hundreds of chunks observes exactly one
        sentinel.
        """
        stream = _ControllableStream()
        signal_event = asyncio.Event()

        collected: list[object] = []

        async def _consume() -> None:
            async for item in _iter_with_signal_break(stream, signal_event):
                collected.append(item)

        task = asyncio.create_task(_consume())

        # Let the wrapper enter its race-wait state BEFORE firing the
        # signal. Without this, the very first iteration sees
        # ``signal_event.is_set()`` and takes the "no sentinel" branch
        # — which is correct behavior in production (terminate landed
        # before iteration started) but defeats this test's premise.
        await asyncio.sleep(0.01)
        signal_event.set()
        await asyncio.sleep(0.01)  # let the wrapper yield the sentinel

        for i in range(50):
            stream.push(f"drain-chunk-{i}")
        stream.end()

        await asyncio.wait_for(task, timeout=2.0)
        assert collected.count(_SIGNAL_SENTINEL) == 1
        # Sentinel came before the drain chunks, not interleaved.
        sentinel_idx = collected.index(_SIGNAL_SENTINEL)
        assert all(c == f"drain-chunk-{i}" for i, c in enumerate(collected[sentinel_idx + 1 :]))

    async def test_disconnect_then_intentional_stop_wakes_silent_provider(self):
        """SHU-803 follow-up (Codex re-review). The Codex-flagged
        regression: ``_iter_with_signal_break`` used to race against
        ``lifecycle.event`` (the omnibus event), which fires on
        ANY signal including ``client_disconnected``. A disconnect
        would consume the wrapper's one-shot sentinel; the
        ``_call_provider`` consumer-loop check then ignored it (the
        reason wasn't an intentional stop), and the wrapper switched
        to plain chunk-await mode. A LATER ``user_terminated`` /
        ``shutdown`` upgrade had no way to wake the wrapper because
        the omnibus event was already set and the wrapper wasn't
        racing it anymore.

        Fix: the wrapper races ``lifecycle.intentional_stop_event``
        instead. ``client_disconnected`` does not set this event;
        the eventual intentional-stop signal sets it for the first
        time, waking the silent provider's race.

        This integration-style test wires the wrapper to a real
        ``StreamLifecycle`` (rather than a bare ``asyncio.Event``)
        so we exercise the actual production event source and catch
        a regression in either the lifecycle's signal() semantics
        OR the wrapper's wiring.
        """
        from shu.services.chat_streaming import StreamLifecycle

        lifecycle = StreamLifecycle(
            stream_id="test-stream", user_id="test-user", conversation_id="test-conv"
        )
        stream = _ControllableStream()  # silent provider — never pushed
        collected: list[object] = []

        async def _consume() -> None:
            async for item in _iter_with_signal_break(stream, lifecycle.intentional_stop_event):
                collected.append(item)
                if item is _SIGNAL_SENTINEL:
                    break

        task = asyncio.create_task(_consume())

        # Let the wrapper enter its race state.
        await asyncio.sleep(0.01)

        # Phase 1: client_disconnected fires. The omnibus event sets
        # but the intentional-stop event does NOT — the wrapper must
        # NOT yield the sentinel here.
        lifecycle.signal("client_disconnected")
        assert lifecycle.event.is_set() is True
        assert lifecycle.intentional_stop_event.is_set() is False

        # Give the wrapper a chance to (incorrectly, if buggy) wake up.
        await asyncio.sleep(0.05)
        assert collected == [], (
            "wrapper consumed its sentinel on client_disconnected; the "
            "later intentional-stop wake-up below would be missed. This "
            "is the exact Codex-flagged regression."
        )

        # Phase 2: a LATER user_terminated upgrades the lifecycle and
        # fires the intentional-stop event for the first time. The
        # wrapper, which was correctly NOT racing the omnibus event,
        # must now wake up and yield the sentinel — proving that the
        # disconnect didn't permanently disable future wake-ups.
        lifecycle.signal("user_terminated")
        assert lifecycle.intentional_stop_event.is_set() is True

        await asyncio.wait_for(task, timeout=1.0)
        assert collected == [_SIGNAL_SENTINEL]
