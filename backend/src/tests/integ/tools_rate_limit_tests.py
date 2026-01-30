"""
Integration test for Tools rate limiting.

This suite enables rate limiting and verifies that rapid consecutive executions
are limited (HTTP 429) when exceeding the per-user-per-tool threshold.
"""

from __future__ import annotations

import asyncio
import os

# Enable rate limiting for this test
os.environ["SHU_ENABLE_API_RATE_LIMITING"] = "1"
# Configure a very small bucket to force 429 on immediate consecutive calls
os.environ["SHU_API_RATE_LIMIT_USER_REQUESTS"] = "1"  # capacity
os.environ["SHU_API_RATE_LIMIT_USER_PERIOD"] = "60"  # seconds (only used to compute refill)

from integ.integration_test_runner import run_integration_tests
from shu.models.plugin_registry import PluginDefinition


async def test_rate_limit_execute(client, db, auth_headers):
    # Arrange: ensure ToolDefinition exists for debug_echo
    row = PluginDefinition(name="debug_echo", version="1", enabled=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        # First call should pass
        body = {"params": {"message": "hello"}}
        r1 = await client.post("/api/v1/tools/debug_echo/execute", json=body, headers=auth_headers)
        assert r1.status_code == 200, r1.text

        # Immediate burst should trigger limiter (capacity=1, ~1s window)
        statuses = []
        for _ in range(4):
            r = await client.post("/api/v1/tools/debug_echo/execute", json=body, headers=auth_headers)
            statuses.append(r.status_code)
        assert any(s == 429 for s in statuses), f"Expected at least one 429 in burst, got {statuses}"
        # Optionally check detail payload if 429 observed
        # (skipping strict shape checks to avoid flakiness across backends)
    finally:
        # Cleanup
        await db.delete(row)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(run_integration_tests([test_rate_limit_execute]))
