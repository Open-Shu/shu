"""Chat latency baseline measurement (SHU-759 AC#8).

Drives N sequential chats through `provider_type=local` against the live
backend and reports p50/p95 of total request duration. Operator runs this
on the pre-refactor branch, captures the numbers, then runs again on the
post-refactor branch and asserts post-p50 ≤ pre-p50 × 1.05.

This module is **not** auto-discovered by `run_all_integration_tests.py`
(which scans only `tests/integ/test_*_integration.py` non-recursively),
so it never runs as part of the regular suite.

Usage (inside the api container)::

    python -m tests.integ.baseline.chat_latency_baseline

Or from the host::

    docker exec shu-api-dev sh -c \\
        "cd /app/src && python -m tests.integ.baseline.chat_latency_baseline"

Defaults: N=10 sequential chats. Override with --n.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.api_helpers import process_streaming_result
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)

# Knob: number of sequential chats per run. Override at the CLI.
DEFAULT_N = 10

# Stash for the CLI override → reachable from the test function (which has a
# fixed signature dictated by the integration framework).
_RUN_N: int = DEFAULT_N


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


async def _ensure_local_provider_type(db) -> None:
    """Idempotently insert the `local` provider type definition.

    Dev DBs are seeded with production provider types (openai, anthropic,
    etc.) but not `local`, even though [local_adapter.py](../../shu/services/providers/adapters/local_adapter.py)
    is registered in code. The baseline needs `local` to bypass real LLM
    calls, so we bootstrap the row here.
    """
    existing = await db.execute(
        text("SELECT 1 FROM llm_provider_type_definitions WHERE key = :k"), {"k": "local"}
    )
    if existing.first():
        return

    now = datetime.now(UTC)
    await db.execute(
        text(
            "INSERT INTO llm_provider_type_definitions "
            "(id, key, display_name, provider_adapter_name, is_active, created_at, updated_at) "
            "VALUES (:id, :key, :display_name, :adapter, :is_active, :created_at, :updated_at)"
        ),
        {
            "id": str(uuid.uuid4()),
            "key": "local",
            "display_name": "Local (test)",
            "adapter": "local",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        },
    )
    await db.commit()
    logger.info("Bootstrapped llm_provider_type_definitions row for key='local'")


async def _create_local_chat_setup(client, db, auth_headers) -> tuple[str, str]:
    """Provision a local-provider model configuration and conversation.

    Returns ``(conversation_id, model_configuration_id)``.
    """
    await _ensure_local_provider_type(db)

    suffix = uuid.uuid4().hex[:8]

    provider_data = {
        "name": f"Test Baseline Local Provider {suffix}",
        "provider_type": "local",
        "api_endpoint": "http://localhost",
        "api_key": "test-baseline",
        "is_active": True,
    }
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code in (200, 201), provider_response.text
    provider_id = extract_data(provider_response)["id"]

    model_data = {
        "model_name": f"local-test-{suffix}",
        "display_name": "Local Test Model (baseline)",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "is_active": True,
    }
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code in (200, 201), model_response.text

    config_data = {
        "name": f"Test Baseline Chat Config {suffix}",
        "description": "Latency baseline configuration",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-baseline",
    }
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in (200, 201), config_response.text
    model_config_id = extract_data(config_response)["id"]

    conversation_data = {
        "title": f"Test Baseline Conversation {suffix}",
        "model_configuration_id": model_config_id,
    }
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code in (200, 201), conv_response.text
    conversation_id = extract_data(conv_response)["id"]

    return conversation_id, model_config_id


async def test_chat_latency_baseline(client, db, auth_headers):
    """Run N sequential chats and report p50/p95.

    Always passes (the value is in the printed measurements, not the
    assertion). Uses `provider_type=local` so the LLM round-trip is the
    deterministic in-process echo at [client.py:980](../../shu/llm/client.py#L980).
    """
    n = _RUN_N
    conversation_id, _ = await _create_local_chat_setup(client, db, auth_headers)

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


class ChatLatencyBaselineSuite(BaseIntegrationTestSuite):
    """Latency baseline runner for SHU-759 AC#8 — operator-driven, not part of CI."""

    def get_test_functions(self) -> list[Callable]:
        return [test_chat_latency_baseline]

    def get_suite_name(self) -> str:
        return "Chat Latency Baseline (SHU-759 AC#8)"

    def get_suite_description(self) -> str:
        return "Sequential N=10 chat latency measurement through provider_type=local"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure baseline chat latency for SHU-759 AC#8."
    )
    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_N,
        help=f"Number of sequential chats to run (default: {DEFAULT_N}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _args = _parse_args()
    # Rebind the module-level _RUN_N so test_chat_latency_baseline picks up the
    # CLI override (the framework dictates the test signature, so we plumb via
    # module state rather than parameters).
    globals()["_RUN_N"] = _args.n
    suite = ChatLatencyBaselineSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
