"""
Integration tests for Tools: registry + loader + executor path.

This suite avoids external provider calls by using the debug_echo plugin.
Gmail digest remains for real-world use but is not exercised here.
"""
from __future__ import annotations
import asyncio
import os, sys
from typing import Any, Dict

# Disable rate limiting for this test module to avoid redis dependency
os.environ.setdefault("SHU_ENABLE_API_RATE_LIMITING", "0")

from sqlalchemy import select

from shu.models.plugin_registry import PluginDefinition
from integ.integration_test_runner import run_integration_tests


async def test_tools_list_and_get(client, db, auth_headers):
    # Arrange: ensure ToolDefinition exists for debug_echo
    created = False
    existing = (await db.execute(select(PluginDefinition).where(PluginDefinition.name == "debug_echo", PluginDefinition.version == "1"))).scalars().first()
    if existing:
        row = existing
        row.enabled = True
        await db.commit()
        await db.refresh(row)
    else:
        row = PluginDefinition(name="debug_echo", version="1", enabled=True)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        created = True

    try:
        # Act: list plugins
        r = await client.get("/api/v1/plugins", headers=auth_headers)
        assert r.status_code == 200, r.text
        names = [t["name"] for t in r.json()["data"]]
        assert "debug_echo" in names

        # Act: get plugin details
        r = await client.get("/api/v1/plugins/debug_echo", headers=auth_headers)
        assert r.status_code == 200, r.text
        plugin = r.json()["data"]
        assert plugin["enabled"] is True
    finally:
        # Cleanup (only delete if we created it here)
        if created:
            await db.delete(row)
            await db.commit()


async def test_tools_execute_echo(client, db, auth_headers):
    # Arrange: ensure ToolDefinition exists for debug_echo
    created = False
    existing = (await db.execute(select(PluginDefinition).where(PluginDefinition.name == "debug_echo", PluginDefinition.version == "1"))).scalars().first()
    if existing:
        row = existing
        row.enabled = True
        await db.commit()
        await db.refresh(row)
    else:
        row = PluginDefinition(name="debug_echo", version="1", enabled=True)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        created = True

    try:
        # Act: execute plugin
        body = {"params": {"message": "hello", "count": 2}}
        r = await client.post("/api/v1/plugins/debug_echo/execute", json=body, headers=auth_headers)
        assert r.status_code == 200, r.text
        payload = r.json()["data"]
        assert payload["status"] == "success"
        assert payload["data"]["echo"]["message"] == "hello"
    finally:
        # Cleanup (only delete if we created it here)
        if created:
            await db.delete(row)
            await db.commit()


if __name__ == "__main__":
    asyncio.run(run_integration_tests([test_tools_list_and_get, test_tools_execute_echo]))


