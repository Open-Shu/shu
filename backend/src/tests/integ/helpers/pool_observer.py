"""Pool checkout observer for SQLAlchemy async engine (SHU-759).

Hooks the SQLAlchemy ``checkout`` and ``checkin`` pool events on a target
engine. While a window is open, tracks:

- ``max_in_window`` — peak concurrent checkouts during the window.
- ``cumulative_hold_seconds`` — area under the active-checkouts curve over
  the window (i.e., total connection-seconds held). One session held for
  100 ms registers 0.1 s; two sessions held simultaneously for 100 ms
  registers 0.2 s.
- ``window_duration_seconds`` — wall-clock window length.

The ratio ``cumulative_hold_seconds / window_duration_seconds`` collapses
to "average concurrent checkouts during the window," which is the headline
metric for the SHU-759 refactor: today the chat request session is held
the entire time the LLM is streaming, so the ratio is ≥1; post-refactor it
should approach 0 in the no-tools / no-RAG path.

Listeners run synchronously on the same event loop the async engine drives,
so no synchronization primitive is needed.

Usage::

    from integ.helpers.pool_observer import PoolObserver
    from shu.core.database import get_async_engine

    with PoolObserver(get_async_engine()) as observer:
        observer.open_window()
        # ... drive a request that streams ...
        stats = observer.close_window()
        assert stats.max_in_window <= 2
        print(f"held {stats.cumulative_hold_seconds:.3f}s over "
              f"{stats.window_duration_seconds:.3f}s window")

For SSE-bracketed measurement, the caller opens the window when the first
``content_delta`` event arrives and closes it on ``final_message``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import event

from shu.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)


@dataclass(frozen=True)
class WindowStats:
    """Snapshot of pool activity captured between open_window and close_window."""

    max_in_window: int
    cumulative_hold_seconds: float
    window_duration_seconds: float

    @property
    def average_concurrent_checkouts(self) -> float:
        """cumulative_hold / window_duration. 0.0 when the window has no duration."""
        if self.window_duration_seconds <= 0:
            return 0.0
        return self.cumulative_hold_seconds / self.window_duration_seconds


class PoolObserver:
    """Tracks SQLAlchemy connection pool checkouts via event listeners.

    Maintains a running ``current_checkouts`` counter. While a window is
    open, captures the peak value AND integrates the active-checkouts curve
    over time to produce ``cumulative_hold_seconds``.

    Limitations:
    - Counts pool-level checkouts on the wrapped engine only. Other engines
      (e.g., a separate one created for migrations or tests) are not observed.
    - Multiple overlapping windows are not supported; a fresh ``open_window``
      resets all captured stats.
    - Time integration is event-driven: it advances the integral on each
      checkout/checkin event and on close_window. Idle stretches between
      events are accounted for at close-time, so the integral is exact for
      step-function load (which connection counts are).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        # Async pool events are attached on the underlying sync engine; this
        # is the canonical pattern documented for AsyncEngine.
        self._sync_target = engine.sync_engine
        self._current_checkouts = 0
        self._window_open = False
        self._max_in_window = 0
        self._cumulative_hold_seconds = 0.0
        self._window_open_time: float | None = None
        self._last_integration_time: float | None = None
        self._listening = False

    def start(self) -> None:
        """Attach checkout/checkin listeners. Idempotent."""
        if self._listening:
            return
        event.listen(self._sync_target, "checkout", self._on_checkout)
        event.listen(self._sync_target, "checkin", self._on_checkin)
        self._listening = True

    def stop(self) -> None:
        """Detach listeners. Idempotent and safe even if start() was not called."""
        if not self._listening:
            return
        try:
            event.remove(self._sync_target, "checkout", self._on_checkout)
            event.remove(self._sync_target, "checkin", self._on_checkin)
        except Exception:
            logger.debug("Failed to remove pool listeners cleanly", exc_info=True)
        self._listening = False

    @property
    def current_checkouts(self) -> int:
        """Live count of pool checkouts observed so far."""
        return self._current_checkouts

    def open_window(self) -> None:
        """Begin recording window stats.

        Resets the integral and the max so concurrent activity already in
        flight at window-open time isn't double-counted.
        """
        now = time.perf_counter()
        self._max_in_window = self._current_checkouts
        self._cumulative_hold_seconds = 0.0
        self._window_open_time = now
        self._last_integration_time = now
        self._window_open = True

    def close_window(self) -> WindowStats:
        """Stop recording and return the captured WindowStats."""
        if self._window_open:
            self._integrate_to(time.perf_counter())
        self._window_open = False

        window_duration = 0.0
        if self._window_open_time is not None:
            window_duration = (self._last_integration_time or self._window_open_time) - self._window_open_time

        return WindowStats(
            max_in_window=self._max_in_window,
            cumulative_hold_seconds=self._cumulative_hold_seconds,
            window_duration_seconds=window_duration,
        )

    def _integrate_to(self, now: float) -> None:
        """Advance the cumulative-hold integral up to ``now``.

        Each session active during the elapsed slice contributes ``elapsed``
        seconds to the integral. Connection counts are step functions, so
        integrating only at event boundaries is exact.
        """
        if not self._window_open or self._last_integration_time is None:
            return
        elapsed = now - self._last_integration_time
        if elapsed > 0:
            self._cumulative_hold_seconds += self._current_checkouts * elapsed
        self._last_integration_time = now

    def _on_checkout(self, dbapi_conn: Any, conn_record: Any, conn_proxy: Any) -> None:
        self._integrate_to(time.perf_counter())
        self._current_checkouts += 1
        if self._window_open and self._current_checkouts > self._max_in_window:
            self._max_in_window = self._current_checkouts

    def _on_checkin(self, dbapi_conn: Any, conn_record: Any) -> None:
        self._integrate_to(time.perf_counter())
        # Defensive: never go negative even if listeners attach mid-flight
        if self._current_checkouts > 0:
            self._current_checkouts -= 1

    def __enter__(self) -> PoolObserver:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
