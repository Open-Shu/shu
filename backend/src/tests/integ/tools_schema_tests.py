"""
Integration tests for Tools JSON Schema validation using debug_echo plugin.
"""
from __future__ import annotations
import asyncio
import os, sys

# Disable rate limiting to isolate validation behavior
os.environ.setdefault("SHU_ENABLE_RATE_LIMITING", "0")

from shu.models.plugin_registry import PluginDefinition
from integ.integration_test_runner import run_integration_tests


async def test_execute_missing_required_param_returns_422(client, db, auth_headers):
    # Arrange: register and enable debug_echo
    row = PluginDefinition(name="debug_echo", version="1", enabled=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        # Act: execute without required 'message'
        body = {"params": {}}
        r = await client.post("/api/v1/tools/debug_echo/execute", json=body, headers=auth_headers)
        assert r.status_code == 422, r.text
        payload = r.json()
        assert "error" in payload or "detail" in payload
    finally:
        await db.delete(row)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(run_integration_tests([test_execute_missing_required_param_returns_422]))


