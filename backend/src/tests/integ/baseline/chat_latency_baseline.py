"""Chat latency baseline measurement (SHU-759 AC#8).

Drives N sequential chats through ``provider_type=local`` against the live
backend and reports p50/p95 of total request duration. Operator runs this
on the pre-refactor branch, captures the numbers, then runs again on the
post-refactor branch and asserts post-p50 ≤ pre-p50 × 1.05.

This module is **not** auto-discovered by ``run_all_integration_tests.py``
(which scans only ``tests/integ/test_*_integration.py`` non-recursively),
so it never runs as part of the regular suite.

Usage (inside the api container)::

    python -m tests.integ.baseline.chat_latency_baseline

Or from the host::

    docker exec shu-api-dev sh -c \\
        "cd /app/src && python -m tests.integ.baseline.chat_latency_baseline"

Defaults: N=10 sequential chats. Override via ``BASELINE_N=20`` env var.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.baseline._setup import create_local_chat_setup
from integ.helpers.api_helpers import process_streaming_result
from integ.helpers.auth import cleanup_framework_test_admin
from shu.core.logging import get_logger

logger = get_logger(__name__)

# Knob: number of sequential chats per run. Override via env var
# (the integration framework's own argparse owns the CLI).
DEFAULT_N = 10
_RUN_N: int = int(os.environ.get("BASELINE_N", DEFAULT_N))


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (matches numpy.percentile default)."""
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


async def test_chat_latency_baseline(client, db, auth_headers):
    """Run N sequential chats and report p50/p95.

    Always passes (the value is in the printed measurements, not the
    assertion). Uses ``provider_type=local`` so the LLM round-trip is the
    deterministic in-process echo at [client.py:980](../../shu/llm/client.py#L980).
    """
    n = _RUN_N
    conversation_id, _ = await create_local_chat_setup(client, db, auth_headers)

    durations_ms: list[float] = []
    logger.info("=== BASELINE RUN START: N=%d sequential chats ===", n)

    for i in range(n):
        message_data = {
            "message": f"Baseline message {i + 1}",
            "rag_rewrite_mode": "no_rag",
        }
        start = time.perf_counter()
        response = await client.post(
            f"/api/v1/chat/conversations/{conversation_id}/send",
            json=message_data,
            headers=auth_headers,
        )
        # Consume the full SSE stream so timing reflects end-to-end duration
        await process_streaming_result(response)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        durations_ms.append(elapsed_ms)
        logger.info("  chat %2d/%d: %7.2f ms", i + 1, n, elapsed_ms)

    p50 = _percentile(durations_ms, 50)
    p95 = _percentile(durations_ms, 95)
    mean = sum(durations_ms) / len(durations_ms)

    summary = {
        "n": n,
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "mean_ms": round(mean, 2),
        "min_ms": round(min(durations_ms), 2),
        "max_ms": round(max(durations_ms), 2),
        "all_ms": [round(d, 2) for d in durations_ms],
    }

    logger.info("=== BASELINE RESULTS ===")
    logger.info(json.dumps(summary, indent=2))
    logger.info("Copy the JSON above into the PR description for AC#8 before/after comparison.")


async def test_zz_teardown_test_admin(client, db, auth_headers):
    """Sentinel teardown — must run last. Deletes the framework-created
    test-admin user so each baseline run leaves the DB clean.
    """
    await cleanup_framework_test_admin(db)


class ChatLatencyBaselineSuite(BaseIntegrationTestSuite):
    """Latency baseline runner for SHU-759 AC#8 — operator-driven, not part of CI."""

    def get_test_functions(self) -> list[Callable]:
        return [test_chat_latency_baseline, test_zz_teardown_test_admin]

    def get_suite_name(self) -> str:
        return "Chat Latency Baseline (SHU-759 AC#8)"

    def get_suite_description(self) -> str:
        return "Sequential N=10 chat latency measurement through provider_type=local"


if __name__ == "__main__":
    suite = ChatLatencyBaselineSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
