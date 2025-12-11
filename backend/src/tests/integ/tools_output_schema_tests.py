"""
Integration test: output schema validation failure should return HTTP 500 with error=output_validation_error.
"""
from __future__ import annotations
import asyncio
import os, sys

# Disable rate limiting to isolate validation behavior
os.environ.setdefault("SHU_ENABLE_RATE_LIMITING", "0")

from shu.models.plugin_registry import PluginDefinition
from integ.integration_test_runner import run_integration_tests


async def test_output_schema_violation_returns_500(client, db, auth_headers):
    # Arrange: register and enable debug_echo
    row = PluginDefinition(name="debug_echo", version="1", enabled=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        # Act: execute with valid inputs but forced invalid output
        body = {"params": {"message": "hello", "force_invalid_output": True}}
        r = await client.post("/api/v1/tools/debug_echo/execute", json=body, headers=auth_headers)
        # Assert: server error due to output validation failure
        assert r.status_code == 500, r.text
        payload = r.json()
        # Error envelope: { error: { code: "HTTP_500", message: { error: "output_validation_error", message: ... } } }
        assert isinstance(payload, dict) and isinstance(payload.get("error"), dict), payload
        err = payload["error"]
        assert err.get("code") == "HTTP_500", payload
        msg = err.get("message") or {}
        assert isinstance(msg, dict) and msg.get("error") == "output_validation_error", payload
    finally:
        await db.delete(row)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(run_integration_tests([test_output_schema_violation_returns_500]))


