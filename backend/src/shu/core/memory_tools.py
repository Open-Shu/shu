"""In-process memory diagnostics (SHU-731).

Three related concerns live here:

1. ``current_rss_bytes()`` — reads VmRSS from ``/proc/self/status`` on Linux
   and falls back to ``resource.getrusage`` on macOS. Cheap enough to call
   per-request; safe to call from any async context.

2. ``trim_memory()`` — returns the freed arena space to the kernel via
   ``malloc_trim(0)``. A GC cycle is run first so freshly-unreachable objects
   have a chance to release their arena pages before we trim. This is the
   only thing that actually reduces RSS on glibc; Python itself never calls
   it. Returns ``(before_rss, after_rss)`` for logging/response bodies.

3. ``TracemallocController`` — thread-safe wrapper around ``tracemalloc``
   that lets the admin heap-stats endpoint start/stop tracing and snapshot
   on demand. Kept as a lazy module-level singleton so the last snapshot
   survives across requests (that's the whole point — we want to diff a
   future snapshot against the one taken before a workload ran).

The ``periodic_trim_loop`` coroutine is an asyncio task factory; the API
lifespan and the standalone worker entrypoint both spawn it when the
``SHU_MEMORY_TRIM_INTERVAL_SECONDS`` setting is > 0. The interval default
is deliberately disabled — tuning is per-deployment.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import resource
import sys
import threading
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from typing import Any

from shu.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# libc + platform helpers
# ---------------------------------------------------------------------------

_LIBC_LOCK = threading.Lock()
_LIBC: ctypes.CDLL | None = None
_MALLOC_TRIM_AVAILABLE: bool | None = None


def _get_libc() -> ctypes.CDLL | None:
    """Return the process libc, or None if not resolvable (e.g. musl, macOS).

    Cached after the first successful load. ``malloc_trim`` is glibc-specific;
    on musl and macOS this returns a handle but the symbol will not exist and
    ``trim_memory`` will fall back to ``gc.collect()`` only.
    """
    global _LIBC  # noqa: PLW0603 — module-level cache for the libc handle
    with _LIBC_LOCK:
        if _LIBC is not None:
            return _LIBC
        # Prefer the explicit soname to avoid symbol resolution against a
        # static binary linked by LD_PRELOAD (jemalloc). glibc still provides
        # malloc_trim even when jemalloc is preloaded, because jemalloc
        # intercepts the public malloc API but not the glibc-private trim.
        # Under jemalloc, malloc_trim is a no-op and we rely on jemalloc's
        # own decay-based release instead.
        for candidate in ("libc.so.6", ctypes.util.find_library("c") or ""):
            if not candidate:
                continue
            try:
                _LIBC = ctypes.CDLL(candidate, use_errno=True)
                return _LIBC
            except OSError:
                continue
        return None


def _malloc_trim_available() -> bool:
    global _MALLOC_TRIM_AVAILABLE  # noqa: PLW0603 — capability cache
    if _MALLOC_TRIM_AVAILABLE is not None:
        return _MALLOC_TRIM_AVAILABLE
    libc = _get_libc()
    if libc is None:
        _MALLOC_TRIM_AVAILABLE = False
        return False
    try:
        libc.malloc_trim.argtypes = [ctypes.c_size_t]
        libc.malloc_trim.restype = ctypes.c_int
        _MALLOC_TRIM_AVAILABLE = True
    except AttributeError:
        _MALLOC_TRIM_AVAILABLE = False
    return _MALLOC_TRIM_AVAILABLE


def current_rss_bytes() -> int:
    """Return resident set size of the current process in bytes.

    Linux: parse ``/proc/self/status`` VmRSS line (authoritative, matches
    ``ps``). macOS: ``resource.ru_maxrss`` is in bytes on Darwin and kilobytes
    on Linux — handled per-platform. Returns 0 on unknown platforms rather
    than raising.
    """
    # Linux: /proc/self/status is cheaper and more accurate than getrusage
    # because getrusage reports *peak* RSS, not current.
    try:
        with open("/proc/self/status", "rb") as fh:
            for line in fh:
                if line.startswith(b"VmRSS:"):
                    parts = line.split()
                    # format: "VmRSS:\t  12345 kB"
                    return int(parts[1]) * 1024
    except FileNotFoundError:
        pass
    # Fallback (macOS and anything without /proc)
    ru = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return int(ru.ru_maxrss)  # bytes
    return int(ru.ru_maxrss) * 1024  # kB → bytes


# ---------------------------------------------------------------------------
# Trim
# ---------------------------------------------------------------------------


@dataclass
class TrimResult:
    before_rss_bytes: int
    after_rss_bytes: int
    gc_collected: int
    malloc_trim_returned: int | None
    malloc_trim_available: bool
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_rss_bytes": self.before_rss_bytes,
            "after_rss_bytes": self.after_rss_bytes,
            "freed_bytes": self.before_rss_bytes - self.after_rss_bytes,
            "gc_collected": self.gc_collected,
            "malloc_trim_returned": self.malloc_trim_returned,
            "malloc_trim_available": self.malloc_trim_available,
            "elapsed_ms": self.elapsed_ms,
        }


def trim_memory(*, run_gc: bool = True) -> TrimResult:
    """Run a GC cycle (optional) then ``malloc_trim(0)`` to release freed
    pages back to the kernel. Safe to call from a sync context; callers in
    async code should wrap in ``asyncio.to_thread`` if the trim has been
    observed to take more than a few ms on the target workload.
    """
    before = current_rss_bytes()
    start = time.perf_counter()
    collected = gc.collect() if run_gc else 0
    libc = _get_libc()
    trim_rc: int | None = None
    available = _malloc_trim_available()
    if available and libc is not None:
        try:
            trim_rc = int(libc.malloc_trim(0))
        except OSError as exc:  # pragma: no cover — defensive
            logger.warning("malloc_trim failed: %s", exc)
            trim_rc = None
    after = current_rss_bytes()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return TrimResult(
        before_rss_bytes=before,
        after_rss_bytes=after,
        gc_collected=collected,
        malloc_trim_returned=trim_rc,
        malloc_trim_available=available,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Periodic trim task
# ---------------------------------------------------------------------------


async def periodic_trim_loop(interval_seconds: float) -> None:
    """Run ``gc.collect() + malloc_trim(0)`` every ``interval_seconds``.

    Two-step release (SHU-731):

    1. ``gc.collect()`` — runs the cyclic collector through generation 2.
       Under sustained profiling load we accumulate reference cycles
       (Pydantic models, async task frames, SQLAlchemy relationship state)
       that gen-0/gen-1 incremental collection doesn't reach until its
       allocation threshold fires. Skipping this step was the root cause
       of the "slow creep" observed in the first scale run: the manual
       ``POST /heap-stats/trim`` freed 73 MB / 6717 objects that the loop
       without ``gc.collect()`` would never have released.
    2. ``malloc_trim(0)`` — returns the now-freed glibc arena pages to the
       kernel. Python never does this itself.

    Execution cost measured in the lab: ~100-120ms end-to-end for a hot
    heap (6.7k objects, 70+ MB freed). Offloaded to a worker thread via
    ``asyncio.to_thread`` so the event loop can serve other callbacks
    during the GIL-holding portions; the stall is still ~0.2% of wall
    clock at a 60s interval, which is well under any request latency
    that would matter.
    """
    if interval_seconds <= 0:
        logger.info("periodic_trim_loop disabled (interval=%s)", interval_seconds)
        return
    if not _malloc_trim_available():
        logger.info("periodic_trim_loop skipped: malloc_trim not available " "(non-glibc libc or jemalloc preloaded)")
        return
    logger.info("periodic_trim_loop started (interval=%ss)", interval_seconds)
    libc = _get_libc()
    assert libc is not None

    def _full_trim() -> tuple[int, int, int, int]:
        before = current_rss_bytes()
        collected = gc.collect()
        rc = int(libc.malloc_trim(0))
        after = current_rss_bytes()
        return before, after, collected, rc

    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("periodic_trim_loop cancelled")
            raise
        try:
            before, after, collected, rc = await asyncio.to_thread(_full_trim)
            freed = before - after
            # Log only when material memory was released — keeps steady-state
            # logs quiet while still surfacing useful signal during bursts.
            if freed >= 5 * 1024 * 1024 or collected >= 100:
                logger.info(
                    "periodic_trim_released",
                    extra={
                        "freed_bytes": freed,
                        "gc_collected": collected,
                        "malloc_trim_returned": rc,
                        "before_rss_bytes": before,
                        "after_rss_bytes": after,
                    },
                )
        except Exception as exc:
            logger.warning("periodic trim failed: %s", exc)


# ---------------------------------------------------------------------------
# Tracemalloc controller
# ---------------------------------------------------------------------------


@dataclass
class _TracemallocState:
    enabled: bool = False
    nframes: int = 1
    last_snapshot: tracemalloc.Snapshot | None = None
    snapshot_at: float = 0.0
    snapshot_label: str | None = None


class TracemallocController:
    """Coordinates tracemalloc across requests.

    Keeps one *baseline* snapshot so subsequent snapshots can be diffed
    against it. Meant to be driven by the admin heap-stats endpoint:

      - POST /heap-stats/tracemalloc/start  → begin tracing
      - POST /heap-stats/tracemalloc/snapshot?label=pre-upload  → baseline
      - (run workload)
      - POST /heap-stats/tracemalloc/snapshot?label=post-upload → diff
      - POST /heap-stats/tracemalloc/stop   → disable + clear

    Thread safety: tracemalloc's own API is process-global and threadsafe;
    we only serialise access to the dataclass.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _TracemallocState()

    def start(self, nframes: int = 1) -> dict[str, Any]:
        with self._lock:
            if self._state.enabled:
                return {"already_enabled": True, "nframes": self._state.nframes}
            tracemalloc.start(nframes)
            self._state.enabled = True
            self._state.nframes = nframes
            return {"already_enabled": False, "nframes": nframes}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            was = self._state.enabled
            if was:
                tracemalloc.stop()
            self._state = _TracemallocState()
            return {"was_enabled": was}

    def is_enabled(self) -> bool:
        return self._state.enabled

    def snapshot(self, label: str | None = None, *, keep_as_baseline: bool = True) -> tracemalloc.Snapshot:
        if not self._state.enabled:
            raise RuntimeError("tracemalloc not enabled; call start() first")
        snap = tracemalloc.take_snapshot()
        # Strip common noise (tracemalloc's own allocations, importlib, linecache).
        snap = snap.filter_traces(
            (
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
                tracemalloc.Filter(False, "<unknown>"),
            )
        )
        if keep_as_baseline:
            with self._lock:
                self._state.last_snapshot = snap
                self._state.snapshot_at = time.time()
                self._state.snapshot_label = label
        return snap

    def top_stats(self, limit: int = 25, group_by: str = "lineno") -> list[dict[str, Any]]:
        if not self._state.enabled:
            raise RuntimeError("tracemalloc not enabled; call start() first")
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics(group_by)[:limit]
        return [
            {
                "source": str(s.traceback),
                "size_bytes": s.size,
                "count": s.count,
                "size_kb": round(s.size / 1024, 1),
            }
            for s in stats
        ]

    def top_diff(self, limit: int = 25, group_by: str = "lineno") -> list[dict[str, Any]]:
        """Diff a fresh snapshot against the stored baseline."""
        if not self._state.enabled:
            raise RuntimeError("tracemalloc not enabled; call start() first")
        if self._state.last_snapshot is None:
            raise RuntimeError("no baseline snapshot; POST /tracemalloc/snapshot first")
        current = tracemalloc.take_snapshot().filter_traces(
            (
                tracemalloc.Filter(False, tracemalloc.__file__),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
                tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
            )
        )
        diff = current.compare_to(self._state.last_snapshot, group_by)[:limit]
        return [
            {
                "source": str(s.traceback),
                "size_diff_bytes": s.size_diff,
                "size_bytes": s.size,
                "count_diff": s.count_diff,
                "count": s.count,
                "size_diff_kb": round(s.size_diff / 1024, 1),
            }
            for s in diff
        ]

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._state.enabled,
                "nframes": self._state.nframes,
                "has_baseline": self._state.last_snapshot is not None,
                "baseline_label": self._state.snapshot_label,
                "baseline_at": self._state.snapshot_at or None,
                "traced_memory": tracemalloc.get_traced_memory() if self._state.enabled else None,
            }


_TRACEMALLOC_CONTROLLER: TracemallocController | None = None
_TRACEMALLOC_LOCK = threading.Lock()


def get_tracemalloc_controller() -> TracemallocController:
    """Singleton accessor — kept module-global so snapshots persist."""
    global _TRACEMALLOC_CONTROLLER  # noqa: PLW0603 — process-wide singleton
    with _TRACEMALLOC_LOCK:
        if _TRACEMALLOC_CONTROLLER is None:
            _TRACEMALLOC_CONTROLLER = TracemallocController()
        return _TRACEMALLOC_CONTROLLER


# ---------------------------------------------------------------------------
# Object/type inventories
# ---------------------------------------------------------------------------


def top_object_types(limit: int = 25) -> list[dict[str, Any]]:
    """Return the ``limit`` most populous Python object types by count.

    This is O(N) over ``gc.get_objects()`` which on a 460k-object heap takes
    ~30-80 ms. Do not call on the request hot path — it's an admin tool.
    ``sys.getsizeof`` is *shallow* (doesn't follow references) so sizes are
    lower bounds; they're still useful as relative signal.
    """
    objects = gc.get_objects()
    counter: Counter[str] = Counter()
    for obj in objects:
        counter[type(obj).__name__] += 1
    most = counter.most_common(limit)
    size_by_type: dict[str, int] = dict.fromkeys([t for t, _ in most], 0)
    for obj in objects:
        name = type(obj).__name__
        if name in size_by_type:
            try:
                size_by_type[name] += sys.getsizeof(obj)
            except Exception:  # pragma: no cover — some objects refuse sizing
                pass
    return [{"type": t, "count": n, "shallow_size_bytes": size_by_type.get(t, 0)} for t, n in most]


def asyncio_task_inventory(limit: int = 25) -> dict[str, Any]:
    """Count outstanding asyncio tasks and name the top coroutines.

    Useful for finding orphaned tasks that hold references to chunk lists
    or httpx responses long after their parent coroutine has moved on.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"running_loop": False, "tasks_total": 0, "tasks": []}
    tasks = asyncio.all_tasks(loop)
    names: Counter[str] = Counter()
    for t in tasks:
        coro = t.get_coro()
        if coro is None:
            names["<no-coro>"] += 1
            continue
        frame = getattr(coro, "cr_code", None) or getattr(coro, "gi_code", None)
        if frame is not None:
            names[f"{frame.co_filename}:{frame.co_name}"] += 1
        else:
            names[type(coro).__name__] += 1
    return {
        "running_loop": True,
        "tasks_total": len(tasks),
        "tasks": [{"where": k, "count": v} for k, v in names.most_common(limit)],
    }
