import asyncio
import logging

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.helpers.auth import create_active_user_headers
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


async def _ensure_plugin_enabled(client, auth_headers, name: str = "gmail_digest"):
    # Sync registry and enable the plugin if present
    await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    await client.patch(f"/api/v1/plugins/admin/{name}/enable", json={"enabled": True}, headers=auth_headers)


async def _create_user_and_get_id(client, admin_headers):
    user_headers = await create_active_user_headers(client, admin_headers, role="regular_user")
    me = await client.get("/api/v1/auth/me", headers=user_headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["data"]["user_id"]
    return user_id


async def test_feed_create_list_identity_missing(client, db, auth_headers):
    """Creating a feed with an owner and auth overlay should show missing identity status when no token exists."""
    await _ensure_plugin_enabled(client, auth_headers, name="gmail_digest")

    owner_user_id = await _create_user_and_get_id(client, auth_headers)

    # Create a schedule for test_schema but require Gmail user auth via explicit params
    resp = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "test_feed_identity_missing",
            "plugin_name": "gmail_digest",
            "params": {"op": "ingest"},
            "interval_seconds": 60,
            "enabled": True,
            "owner_user_id": owner_user_id,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    sched = extract_data(resp)

    # List by owner; identity_status should reflect missing identity
    list_resp = await client.get(f"/api/v1/plugins/admin/feeds?owner_user_id={owner_user_id}", headers=auth_headers)
    assert list_resp.status_code == 200, list_resp.text
    rows = extract_data(list_resp)
    match = next((r for r in rows if r.get("id") == sched["id"]), None)
    assert match is not None, f"Created schedule not found in list: {rows}"
    assert match.get("owner_user_id") == owner_user_id
    assert match.get("identity_status") in ("missing_identity", "unknown")  # allow unknown if provider probing changes


async def test_run_now_with_missing_identity_fails(client, db, auth_headers):
    """Run-now + run-pending should fail with identity_required when auth overlay demands user token and none exists."""
    await _ensure_plugin_enabled(client, auth_headers)

    owner_user_id = await _create_user_and_get_id(client, auth_headers)

    create = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "test_run_now_identity_required",
            "plugin_name": "gmail_digest",
            "params": {"op": "ingest"},
            "interval_seconds": 60,
            "enabled": True,
            "owner_user_id": owner_user_id,
        },
        headers=auth_headers,
    )
    assert create.status_code == 200, create.text
    sched = extract_data(create)

    rn = await client.post(f"/api/v1/plugins/admin/feeds/{sched['id']}/run-now", headers=auth_headers)
    assert rn.status_code == 200, rn.text
    exec_rec = extract_data(rn)

    # Force-run the pending execution synchronously
    rpend = await client.post(
        "/api/v1/plugins/admin/executions/run-pending",
        json={"limit": 5, "execution_id": exec_rec["id"]},
        headers=auth_headers,
    )
    assert rpend.status_code == 200, rpend.text

    # Fetch execution result
    getx = await client.get(f"/api/v1/plugins/admin/executions/{exec_rec['id']}", headers=auth_headers)
    assert getx.status_code == 200, getx.text
    x = extract_data(getx)
    assert x.get("status") == "failed", x
    # Error may be set on either top-level error or result.error per code path
    err = x.get("error") or (x.get("result") or {}).get("error")
    assert err == "identity_required", x


async def test_update_owner_assignment(client, db, auth_headers):
    """PATCHing owner_user_id should move ownership between users."""
    await _ensure_plugin_enabled(client, auth_headers, name="test_schema")

    owner_1 = await _create_user_and_get_id(client, auth_headers)
    owner_2 = await _create_user_and_get_id(client, auth_headers)

    create = await client.post(
        "/api/v1/plugins/admin/feeds",
        json={
            "name": "test_owner_move",
            "plugin_name": "test_schema",
            "params": {"q": "owner-1"},
            "interval_seconds": 3600,
            "enabled": True,
            "owner_user_id": owner_1,
        },
        headers=auth_headers,
    )
    assert create.status_code == 200, create.text
    sched = extract_data(create)

    # Move ownership
    upd = await client.patch(
        f"/api/v1/plugins/admin/feeds/{sched['id']}",
        json={"owner_user_id": owner_2},
        headers=auth_headers,
    )
    assert upd.status_code == 200, upd.text
    updated = extract_data(upd)
    assert updated.get("owner_user_id") == owner_2

    # Verify via list filter
    lst = await client.get(f"/api/v1/plugins/admin/feeds?owner_user_id={owner_2}", headers=auth_headers)
    assert lst.status_code == 200, lst.text
    rows = extract_data(lst)
    ids = {r.get("id") for r in rows}
    assert sched["id"] in ids


class FeedOwnershipIdentitySuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_feed_create_list_identity_missing,
            test_run_now_with_missing_identity_fails,
            test_update_owner_assignment,
        ]

    def get_suite_name(self) -> str:
        return "Feed Ownership & Identity"

    def get_suite_description(self) -> str:
        return "Validates feed owner semantics, identity-status, and identity preflight gating."


if __name__ == "__main__":
    create_test_runner_script(FeedOwnershipIdentitySuite, globals())

