"""Chat pool-pressure baseline measurement (SHU-759 AC#1).

Drives N sequential chats and, for each, brackets a PoolObserver window
across the **full request** — from immediately before ``client.post`` to
after the SSE response stream is fully drained. This captures the
session-held time across the chat's entire lifecycle.

For each iteration we record:

- ``cumulative_hold_seconds`` — total connection-seconds the pool held a
  session during the request
- ``window_duration_seconds`` — wall-clock request length
- ``avg_concurrent`` — cumulative_hold / window_duration; ≈ 1.0 today (one
  session held throughout); should drop toward 0 post-SHU-759

Pre-refactor expectation: ``avg_concurrent ≈ 1.0`` and
``cumulative_hold_seconds`` ≈ ``window_duration_seconds`` because the
request-scoped session is held by FastAPI's dependency cleanup for the
entire SSE generator lifetime — exactly the behavior SHU-759 fixes.

Post-refactor expectation (AC#1): ``cumulative_hold_seconds`` near zero
even when window duration is large, because the session is released
before the LLM stream begins.

For meaningful numbers on the local provider, set
``SHU_LOCAL_STREAM_TEST_CHUNK_DELAY_MS=200`` (or similar) — without it the
local provider's stream is sub-millisecond and the metric is dominated by
prepare + finalize phases.

A narrower "SSE-bracketed" window (first content_delta → final_message)
was tried but registered ~0ms because httpx buffers SSE chunks. The
whole-request window is more honest: it directly answers "how long is a
pool session held per chat?"

Usage (inside the api container)::

    SHU_LOCAL_STREAM_TEST_CHUNK_DELAY_MS=200 \\
        python -m tests.integ.baseline.pool_pressure_baseline

Or from the host::

    docker exec -e SHU_LOCAL_STREAM_TEST_CHUNK_DELAY_MS=200 shu-api-dev sh -c \\
        "cd /app/src && python -m tests.integ.baseline.pool_pressure_baseline"

Defaults: N=10 iterations. Override via ``BASELINE_N=20`` env var.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable

import httpx

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.baseline._setup import create_local_chat_setup
from integ.helpers.auth import cleanup_framework_test_admin
from integ.helpers.pool_observer import PoolObserver, WindowStats
from shu.core.database import get_async_engine
from shu.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_N = 10
# Override iteration count via env var; the integration framework's own
# argparse owns the CLI, so we can't add our own flag.
_RUN_N: int = int(os.environ.get("BASELINE_N", DEFAULT_N))


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[int(rank)]
    fraction = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * fraction


def _parse_sse_event(line: str) -> dict | None:
    """Parse a ``data: {json}`` SSE line. Returns None for non-data lines or [DONE]."""
    if not line.startswith("data: "):
        return None
    payload = line[len("data: "):].strip()
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


async def _measure_one_chat(
    client: httpx.AsyncClient,
    observer: PoolObserver,
    conversation_id: str,
    auth_headers: dict,
    iteration: int,
) -> tuple[WindowStats, dict[str, int]]:
    """Drive a single chat with the observer bracketing the entire request.

    Window opens immediately before sending the request and closes after
    the SSE response stream is fully drained (including ``[DONE]``). This
    captures the session-held time across the full chat lifecycle —
    prepare + LLM-stream + finalize today, just prepare + finalize after
    SHU-759. The pre/post delta is the "time savings" metric.

    A narrower SSE-bracketed window (first content_delta → final_message)
    was tried but registered as ~0ms because httpx's aiter_lines buffers
    chunks and the events arrive at the client in a single batch. The
    whole-request window is more honest: it directly answers "how long is
    a pool session held per chat?"

    Also returns SSE event counts so we can confirm the chat actually
    streamed (sanity check against silent failures).
    """
    message_data = {
        "message": f"Pool pressure iter {iteration}",
        "rag_rewrite_mode": "no_rag",
    }
    url = f"/api/v1/chat/conversations/{conversation_id}/send"

    sse_event_counts: dict[str, int] = {}
    observer.open_window()
    try:
        async with client.stream("POST", url, json=message_data, headers=auth_headers) as response:
            assert response.status_code == 200, f"Chat send failed: {response.status_code}"
            async for line in response.aiter_lines():
                event = _parse_sse_event(line)
                if event is None:
                    continue
                event_type = event.get("event") or "unknown"
                sse_event_counts[event_type] = sse_event_counts.get(event_type, 0) + 1
    finally:
        stats = observer.close_window()

    if "content_delta" not in sse_event_counts:
        raise RuntimeError(
            f"Chat iteration {iteration}: stream produced no content_delta events; "
            f"event counts: {sse_event_counts}"
        )

    return stats, sse_event_counts


async def test_pool_pressure_baseline(client, db, auth_headers):
    """Run N sequential chats with PoolObserver bracketing each stream window.

    Reports per-iteration stats and aggregate metrics. Always passes — the
    value is in the printed numbers, captured for PR before/after comparison.
    """
    n = _RUN_N
    conversation_id, _ = await create_local_chat_setup(client, db, auth_headers)

    engine = get_async_engine()
    iterations: list[WindowStats] = []

    logger.info("=== POOL PRESSURE BASELINE RUN START: N=%d iterations ===", n)

    # Use a fresh PoolObserver per iteration. A single shared observer
    # accumulated counter drift across iterations under the framework's HTTP
    # client lifecycle (likely connection-keepalive interaction with the
    # async session pool). Per-iteration observers each see a balanced
    # checkout/checkin cycle, giving consistent numbers.
    for i in range(n):
        with PoolObserver(engine) as observer:
            stats, event_counts = await _measure_one_chat(
                client, observer, conversation_id, auth_headers, i + 1
            )
        iterations.append(stats)
        logger.info(
            "  iter %2d/%d: held=%.4fs over %.4fs request (avg_concurrent=%.2f) "
            "[content_deltas=%d, final_messages=%d]",
            i + 1,
            n,
            stats.cumulative_hold_seconds,
            stats.window_duration_seconds,
            stats.average_concurrent_checkouts,
            event_counts.get("content_delta", 0),
            event_counts.get("final_message", 0),
        )

    max_values = [s.max_in_window for s in iterations]
    hold_values = [s.cumulative_hold_seconds for s in iterations]
    duration_values = [s.window_duration_seconds for s in iterations]
    avg_concurrent_values = [s.average_concurrent_checkouts for s in iterations]

    summary = {
        "n": n,
        "max_in_window": {
            "min": min(max_values),
            "max": max(max_values),
            "p50": _percentile([float(v) for v in max_values], 50),
        },
        "cumulative_hold_seconds": {
            "p50": round(_percentile(hold_values, 50), 4),
            "p95": round(_percentile(hold_values, 95), 4),
            "mean": round(sum(hold_values) / n, 4),
            "total_across_iterations": round(sum(hold_values), 4),
        },
        "window_duration_seconds": {
            "p50": round(_percentile(duration_values, 50), 4),
            "p95": round(_percentile(duration_values, 95), 4),
            "mean": round(sum(duration_values) / n, 4),
        },
        "average_concurrent_checkouts": {
            "p50": round(_percentile(avg_concurrent_values, 50), 3),
            "mean": round(sum(avg_concurrent_values) / n, 3),
        },
        "per_iteration": [
            {
                "max_in_window": s.max_in_window,
                "cumulative_hold_seconds": round(s.cumulative_hold_seconds, 4),
                "window_duration_seconds": round(s.window_duration_seconds, 4),
                "avg_concurrent": round(s.average_concurrent_checkouts, 2),
            }
            for s in iterations
        ],
    }

    logger.info("=== POOL PRESSURE BASELINE RESULTS ===")
    logger.info(json.dumps(summary, indent=2))
    logger.info(
        "Headline pre-refactor metric: cumulative_hold_seconds total = %.3fs over %d chats. "
        "Post-refactor target: near zero (no session held during LLM stream window).",
        sum(hold_values),
        n,
    )


async def test_zz_teardown_test_admin(client, db, auth_headers):
    """Sentinel teardown — must run last. Deletes the framework-created
    test-admin user so each baseline run leaves the DB clean.
    """
    await cleanup_framework_test_admin(db)


class PoolPressureBaselineSuite(BaseIntegrationTestSuite):
    """Pool pressure baseline runner for SHU-759 AC#1 — operator-driven, not part of CI."""

    def get_test_functions(self) -> list[Callable]:
        return [test_pool_pressure_baseline, test_zz_teardown_test_admin]

    def get_suite_name(self) -> str:
        return "Chat Pool Pressure Baseline (SHU-759 AC#1)"

    def get_suite_description(self) -> str:
        return "Per-stream-window pool checkout + cumulative hold time measurement"


if __name__ == "__main__":
    suite = PoolPressureBaselineSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
