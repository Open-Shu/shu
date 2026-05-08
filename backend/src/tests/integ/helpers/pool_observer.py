"""Pool checkout observer for SQLAlchemy async engine (SHU-759).

Used by chat session-release tests to assert that pool checkouts during the
LLM streaming window stay below a known small constant. Hooks the SQLAlchemy
``checkout`` and ``checkin`` pool events on a target engine and tracks both a
running count and the maximum observed within an opened window.

Listeners run synchronously on the same event loop the async engine drives,
so no synchronization primitive is needed.

Usage::

    from integ.helpers.pool_observer import PoolObserver
    from shu.core.database import get_async_engine

    with PoolObserver(get_async_engine()) as observer:
        observer.open_window()
        # ... drive a request that streams ...
        max_during_window = observer.close_window()
        assert max_during_window <= 2

For SSE-bracketed measurement, the caller opens the window when the first
``content_delta`` event arrives and closes it on ``final_message``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class PoolObserver:
    """Tracks SQLAlchemy connection pool checkouts via event listeners.

    Maintains a running ``current_checkouts`` counter and, when a window is
    opened, captures the maximum value observed during that window.

    Limitations:
    - Counts pool-level checkouts on the wrapped engine only. Other engines
      (e.g., a separate one created for migrations or tests) are not observed.
    - Multiple overlapping windows are not supported; a fresh ``open_window``
      resets the captured maximum.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        # Async pool events are attached on the underlying sync engine; this
        # is the canonical pattern documented for AsyncEngine.
        self._sync_target = engine.sync_engine
        self._current_checkouts = 0
        self._window_open = False
        self._max_in_window = 0
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
        """Begin recording the max checkouts observed.

        Resets ``max_in_window`` to the current count so concurrent activity
        already in flight at window-open time isn't double-counted.
        """
        self._max_in_window = self._current_checkouts
        self._window_open = True

    def close_window(self) -> int:
        """Stop recording and return max checkouts observed during the window."""
        self._window_open = False
        return self._max_in_window

    def _on_checkout(self, dbapi_conn: Any, conn_record: Any, conn_proxy: Any) -> None:
        self._current_checkouts += 1
        if self._window_open and self._current_checkouts > self._max_in_window:
            self._max_in_window = self._current_checkouts

    def _on_checkin(self, dbapi_conn: Any, conn_record: Any) -> None:
        # Defensive: never go negative even if listeners attach mid-flight
        if self._current_checkouts > 0:
            self._current_checkouts -= 1

    def __enter__(self) -> PoolObserver:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
