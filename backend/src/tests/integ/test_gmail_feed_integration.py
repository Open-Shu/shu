"""
Integration test: gmail_digest plugin executed via Plugin Feeds endpoints.
- Creates a KB
- Creates a feed for gmail_digest using domain delegation or configured OAuth
- Triggers run-now and runs pending executions
- Verifies execution completes and returns expected keys

Requires:
- GOOGLE_SERVICE_ACCOUNT_JSON set (or GOOGLE_SERVICE_ACCOUNT_FILE) for domain delegation OR a connected OAuth account
- TEST_GOOGLE_IMPERSONATE_EMAIL or GOOGLE_ADMIN_USER_EMAIL set for domain delegation
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from integ.integration_test_runner import run_integration_tests


async def _ensure_tool_enabled(client, db, auth_headers, name: str = "gmail_digest"):
    await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    await client.patch(f"/api/v1/plugins/admin/{name}/enable", json={"enabled": True}, headers=auth_headers)


async def test_gmail_feed_run_now_domain_delegate(client, db, auth_headers):
    await _ensure_tool_enabled(client, db, auth_headers)

    kb_id = None
    schedule_id = None
    try:
        # Create a KB
        kb_payload = {
            "name": f"Gmail Feed KB {uuid.uuid4().hex[:6]}",
            "description": "KB for gmail feed execution",
            "sync_enabled": True,
        }
        r = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
        assert r.status_code == 201, r.text
        kb_id = (r.json().get("data") or {}).get("id")
        assert kb_id

        # Resolve impersonation subject if using domain delegation
        from shu.core.config import get_settings_instance

        settings = get_settings_instance()
        subject = os.getenv("TEST_GOOGLE_IMPERSONATE_EMAIL") or (settings.google_admin_user_email or None)

        # Create feed
        feed_body: dict[str, Any] = {
            "name": f"Gmail Feed {uuid.uuid4().hex[:6]}",
            "plugin_name": "gmail_digest",
            "params": {
                "op": "ingest",
                "kb_id": kb_id,
                # UI overlay: domain delegation with explicit subject, if available
                "__host": {"auth": {"google": {"mode": "domain_delegate", "subject": subject}}} if subject else {},
                # scope and limits
                "since_hours": 24,
                "max_results": 10,
            },
            "interval_seconds": 3600,
            "enabled": True,
        }
        fr = await client.post("/api/v1/plugins/admin/feeds", json=feed_body, headers=auth_headers)
        assert fr.status_code == 200, fr.text
        sched = fr.json().get("data") or {}
        schedule_id = sched.get("id")
        assert schedule_id

        # Run now -> creates a pending execution
        rn = await client.post(f"/api/v1/plugins/admin/feeds/{schedule_id}/run-now", headers=auth_headers)
        assert rn.status_code == 200, rn.text

        # Process the pending execution
        rp = await client.post(
            "/api/v1/plugins/admin/executions/run-pending",
            json={"limit": 1, "schedule_id": schedule_id},
            headers=auth_headers,
        )
        assert rp.status_code == 200, rp.text

        # Fetch executions for this schedule
        ge = await client.get(f"/api/v1/plugins/admin/executions?schedule_id={schedule_id}", headers=auth_headers)
        assert ge.status_code == 200, ge.text
        rows = ge.json().get("data") or []
        assert len(rows) >= 1
        last = rows[-1]
        assert last.get("status") in ("completed",)
        result = last.get("result") or {}
        data = result.get("data") or {}
        # Expect keys from gmail ingest (delta-friendly)
        assert "history_id" in data or "count" in data, data
    finally:
        # Cleanup: delete feed (also deletes executions), then delete KB
        try:
            if schedule_id:
                dr = await client.delete(f"/api/v1/plugins/admin/feeds/{schedule_id}", headers=auth_headers)
                assert dr.status_code in (200, 204), dr.text
        except Exception:
            pass
        try:
            if kb_id:
                kr = await client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
                assert kr.status_code in (200, 204), kr.text
        except Exception:
            pass


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_integration_tests([test_gmail_feed_run_now_domain_delegate], enable_file_logging=True))
