from __future__ import annotations
import logging
from typing import List
import sys, os

from integ.helpers.api_helpers import list_subscription_plugins
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


async def test_subscriptions_crud_filters_union(client, db, auth_headers):
    """Subscriptions CRUD affects consent-scopes union for the user."""
    await _enable_plugin(client, auth_headers, "gmail_digest")
    await _enable_plugin(client, auth_headers, "gdrive_files")

    # Initially (no subscriptions): union includes both gmail + drive
    resp0 = await client.get("/api/v1/host/auth/consent-scopes", params={"provider": "google"}, headers=auth_headers)
    assert resp0.status_code == 200, resp0.text
    scopes0 = extract_data(resp0).get("scopes") or []
    assert scopes0 == []  # no subscription = no scopes

    assert set() == await list_subscription_plugins(client, "google", auth_headers)

    # Subscribe only to gmail_digest
    body = {"provider": "google", "plugin_name": "gmail_digest"}
    resp = await client.post("/api/v1/host/auth/subscriptions", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    assert {"gmail_digest"} == await list_subscription_plugins(client, "google", auth_headers)

    # Union should now include gmail scopes but not drive
    resp1 = await client.get("/api/v1/host/auth/consent-scopes", params={"provider": "google"}, headers=auth_headers)
    assert resp1.status_code == 200, resp1.text
    scopes1 = extract_data(resp1).get("scopes") or []
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes1
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes1
    assert "https://www.googleapis.com/auth/drive" not in scopes1

    # Subscribe gdrive_files as well; union should include drive now
    body2 = {"provider": "google", "plugin_name": "gdrive_files"}
    resp2 = await client.post("/api/v1/host/auth/subscriptions", json=body2, headers=auth_headers)
    assert resp2.status_code == 200, resp2.text

    resp2b = await client.get("/api/v1/host/auth/consent-scopes", params={"provider": "google"}, headers=auth_headers)
    assert resp2b.status_code == 200, resp2b.text
    scopes2 = extract_data(resp2b).get("scopes") or []
    assert "https://www.googleapis.com/auth/drive" in scopes2

    assert {"gmail_digest", "gdrive_files"} == await list_subscription_plugins(client, "google", auth_headers)

    # Delete drive subscription; union should drop drive again
    del_body = {"provider": "google", "plugin_name": "gdrive_files"}
    resp_del = await client.request("DELETE", "/api/v1/host/auth/subscriptions", json=del_body, headers=auth_headers)
    assert resp_del.status_code == 200, resp_del.text

    resp3 = await client.get("/api/v1/host/auth/consent-scopes", params={"provider": "google"}, headers=auth_headers)
    assert resp3.status_code == 200, resp3.text
    scopes3 = extract_data(resp3).get("scopes") or []
    assert "https://www.googleapis.com/auth/drive" not in scopes3


class ConnectedAccountsTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_subscriptions_crud_filters_union,
        ]

    def get_suite_name(self) -> str:
        return "Connected Accounts & Subscriptions Integration Tests"

    def get_suite_description(self) -> str:
        return "Tests for consent scopes union and plugin subscriptions CRUD"



if __name__ == "__main__":
    suite = ConnectedAccountsTestSuite()
    exit_code = suite.run()
    import sys as _sys
    _sys.exit(exit_code)
