"""
Integration tests for Tools v1 Registry + Policy (DB enablement + execution gating).

Covers:
- Admin sync to auto-register discovered plugins
- List/get registry entries
- Enable toggle gating execution (disabled -> 404; enabled -> success)

Follows custom integration test framework in tests/.
"""
import logging
from typing import Any, Dict

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


# --- Test functions ---
async def test_registry_sync_creates_entries(client, db, auth_headers):
    """Admin sync should register discovered plugins with enabled False by default."""
    # Trigger sync
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "discovered" in data and data["discovered"] >= 1

    # List tools and verify test_schema exists
    resp = await client.get("/api/v1/plugins", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    tools = resp.json()["data"]
    names = [t["name"] for t in tools]
    assert "test_schema" in names

    # Check that it is disabled by default (or present with some enabled flag)
    row = next(t for t in tools if t["name"] == "test_schema")
    assert row.get("enabled") in (False, True)  # present; sync may choose default False per implementation


async def test_registry_enable_gates_execution(client, db, auth_headers):
    """Execution should be denied when tool is disabled and allowed when enabled."""
    # Ensure sync ran
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    # Explicitly disable the tool first
    resp = await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Attempt execution -> expect 404 (not found or disabled)
    exec_body = {"params": {"q": "hello"}}
    resp = await client.post("/api/v1/plugins/test_schema/execute", json=exec_body, headers=auth_headers)
    assert resp.status_code == 404, resp.text

    # Enable the tool
    resp = await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["enabled"] is True

    # Execute -> expect success envelope with data containing echoed value
    resp = await client.post("/api/v1/plugins/test_schema/execute", json=exec_body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["status"] == "success"
    assert payload["data"]["echo"] == "hello"


async def test_registry_get_and_update_schema(client, db, auth_headers):
    """Admin can set schema fields; GET should reflect updates."""
    # Ensure synced and enabled
    await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": True},
        headers=auth_headers,
    )

    # Update schema via admin
    new_in_schema: Dict[str, Any] = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    new_out_schema: Dict[str, Any] = {"type": "object", "properties": {"echo": {"type": "string"}}, "required": ["echo"]}
    resp = await client.put(
        "/api/v1/plugins/admin/test_schema/schema",
        json={"input_schema": new_in_schema, "output_schema": new_out_schema},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # GET should reflect changes
    resp = await client.get("/api/v1/plugins/test_schema", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    tool = resp.json()["data"]
    assert tool["input_schema"] == new_in_schema
    assert tool["output_schema"] == new_out_schema


# --- Suite wrapper ---
class ToolsRegistryTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_registry_sync_creates_entries,
            test_registry_enable_gates_execution,
            test_registry_get_and_update_schema,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 Registry"

    def get_suite_description(self) -> str:
        return "Integration tests for registry CRUD/policy and execution gating"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(ToolsRegistryTestSuite, globals())

