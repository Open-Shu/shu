from __future__ import annotations
import logging
from typing import List
import sys, os

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


async def _enable_plugin(client, auth_headers, name: str):
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        f"/api/v1/plugins/admin/{name}/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_execute_denied_when_unsubscribed(client, db, auth_headers):
    """Unsubscribed plugin execution (user-mode) should return 403 subscription_required when any subscriptions exist for provider."""
    await _enable_plugin(client, auth_headers, "gmail_digest")
    await _enable_plugin(client, auth_headers, "gdrive_files")

    # Create a subscription for gmail_digest only
    body = {"provider": "google", "plugin_name": "gmail_digest"}
    resp = await client.post("/api/v1/host/auth/subscriptions", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    # Attempt to execute gdrive_files which is not subscribed -> expect 403 subscription_required
    exec_body = {"params": {"container_id": "dummy", "op": "ingest"}}
    resp2 = await client.post("/api/v1/plugins/gdrive_files/execute", json=exec_body, headers=auth_headers)
    assert resp2.status_code == 403, resp2.text
    data2 = resp2.json() if resp2.headers.get("content-type", "").startswith("application/json") else {}
    # Envelope: {"error": {"code": "HTTP_403", "message": {"error": {"code": "subscription_required", ...}}, "details": {}}}
    err_obj = ((data2.get("error") or {}).get("message") or {}).get("error") if isinstance(data2.get("error"), dict) else None
    assert isinstance(err_obj, dict), data2
    assert err_obj.get("code") == "subscription_required", data2


async def test_scheduler_denied_when_unsubscribed(client, db, auth_headers):
    """Scheduled execution for unsubscribed plugin should fail with subscription_required."""
    await _enable_plugin(client, auth_headers, "gmail_digest")
    await _enable_plugin(client, auth_headers, "gdrive_files")

    # Subscribe only to gmail_digest
    resp = await client.post("/api/v1/host/auth/subscriptions", json={"provider": "google", "plugin_name": "gmail_digest"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    # Create a feed for gdrive_files (unsubscribed)
    feed_body = {
        "name": "Sub Enforcement Test Feed",
        "plugin_name": "gdrive_files",
        "params": {},
        "interval_seconds": 60,
        "enabled": True,
    }
    fr = await client.post("/api/v1/plugins/admin/feeds", json=feed_body, headers=auth_headers)
    assert fr.status_code == 200, fr.text
    sched = extract_data(fr)
    schedule_id = sched.get("id")
    assert schedule_id

    # Run now to create pending execution
    rn = await client.post(f"/api/v1/plugins/admin/feeds/{schedule_id}/run-now", headers=auth_headers)
    assert rn.status_code == 200, rn.text

    # Process the pending execution
    rp = await client.post("/api/v1/plugins/admin/executions/run-pending", json={"limit": 1, "schedule_id": schedule_id}, headers=auth_headers)
    assert rp.status_code == 200, rp.text

    # Verify execution failed with subscription_required
    ge = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={schedule_id}", headers=auth_headers)
    assert ge.status_code == 200, ge.text
    rows = extract_data(ge) or []
    assert len(rows) >= 1
    last = rows[-1]
    assert last.get("plugin_name") == "gdrive_files"
    assert last.get("status") in ("failed",)
    assert last.get("error") == "subscription_required"


class SubscriptionEnforcementTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_execute_denied_when_unsubscribed,
            test_scheduler_denied_when_unsubscribed,
        ]

    def get_suite_name(self) -> str:
        return "Plugin Subscription Enforcement"

    def get_suite_description(self) -> str:
        return "Denial of execution when plugin is unsubscribed for provider"


if __name__ == "__main__":
    suite = SubscriptionEnforcementTestSuite()
    exit_code = suite.run()
    import sys as _sys
    _sys.exit(exit_code)

