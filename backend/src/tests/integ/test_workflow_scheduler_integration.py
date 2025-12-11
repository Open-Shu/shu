"""
Integration tests for minimal workflow persistence/scheduler (Option A Step 4).

Covers:
- Execution persistence on direct /plugins/{name}/execute
- Interval schedule creation, enqueue of due schedules, and running pending executions
"""
import logging
from typing import Any, Dict

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


# --- Helpers ---
async def _ensure_tool_enabled(client, auth_headers, name: str = "test_schema"):
    await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    await client.patch(f"/api/v1/plugins/admin/{name}/enable", json={"enabled": True}, headers=auth_headers)


# --- Test functions ---
async def test_execute_persists_record(client, db, auth_headers):
    """Direct execution should create a PluginExecution row with completed status."""
    await _ensure_tool_enabled(client, auth_headers)

    # List executions before
    resp = await client.get("/api/v1/plugins/admin/executions", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    before = resp.json()["data"]

    # Execute tool
    exec_body = {"params": {"q": "hello"}}
    resp = await client.post("/api/v1/plugins/test_schema/execute", json=exec_body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["status"] == "success"
    assert payload["data"]["echo"] == "hello"

    # List executions after
    resp = await client.get("/api/v1/plugins/admin/executions", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    after = resp.json()["data"]
    assert len(after) >= len(before)
    # Verify last execution matches
    last = after[0]
    assert last["plugin_name"] == "test_schema"
    assert last["status"] in ("completed", "failed")


async def test_schedule_enqueue_and_run(client, db, auth_headers):
    """Create an interval schedule, enqueue due runs, and process pending executions."""
    await _ensure_tool_enabled(client, auth_headers)

    # Create schedule
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "Test Schedule - Tools v1",
            "plugin_name": "test_schema",
            "params": {"q": "world"},
            "interval_seconds": 60,
            "enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched = resp.json()["data"]

    # Enqueue due schedules
    resp = await client.post("/api/v1/plugins/admin/feeds/run-due", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    stats = resp.json()["data"]
    assert stats["enqueued"] >= 1

    # Run pending
    resp = await client.post(
        "/api/v1/plugins/admin/executions/run-pending",
        json={"limit": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ran_stats = resp.json()["data"]
    assert ran_stats["attempted"] >= 1

    # Verify at least one completed execution with expected output
    resp = await client.get("/api/v1/plugins/admin/executions", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    match = [r for r in rows if r["plugin_name"] == "test_schema" and r.get("result", {}).get("data", {}).get("echo") == "world"]
    assert len(match) >= 1
    assert match[-1]["status"] in ("completed",)


# --- Suite wrapper ---
class PluginsWorkflowSchedulerTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_execute_persists_record,
            test_schedule_enqueue_and_run,
        ]

    def get_suite_name(self) -> str:
        return "Plugins v1 Workflow Scheduler"

    def get_suite_description(self) -> str:
        return "Integration tests for minimal plugin execution persistence and interval scheduler"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(PluginsWorkflowSchedulerTestSuite, globals())

