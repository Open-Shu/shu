from unittest.mock import patch

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.helpers.api_helpers import execute_plugin
from integ.response_utils import extract_data


# --- Test functions ---
async def test_gmail_action_preview_returns_plan(client, db, auth_headers):
    # Sync and enable gmail_digest
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/gmail_digest/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # does not work, since the auth isn't set up
    resp = await execute_plugin(client, "gmail_digest", auth_headers)
    assert resp.status_code == 403, resp.text
    assert (
        extract_data(resp).get("error", {}).get("message", {}).get("error", {}).get("message", "")
        == "Provider account (google) connected but missing required scopes for this operation. Reconnect via the plugin panel."
    )

    # mock the provider response as we don't actually want to interface with google credentials
    with patch("shu.providers.google.auth_adapter.GoogleAuthAdapter.exchange_code") as mock_adapter:
        mock_adapter.return_value = {
            "access_token": "access_token",
            "refresh_token": "refresh_token",
            "expires_in": None,
        }
        resp = await client.post(
            "/api/v1/host/auth/exchange",
            json={
                "provider": "google",
                "code": "somecoed",
                "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

    # Preview an action plan (no side effects)
    resp = await execute_plugin(client, "gmail_digest", auth_headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    body_data = body.get("data") or {}
    assert body_data.get("status") == "success", body
    data = body_data.get("data") or {}
    plan = data.get("plan") or {}
    assert plan.get("action") == "mark_read", plan
    assert plan.get("message_count") == 2, plan
    assert plan.get("requires_approval") is True, plan


# --- Suite wrapper ---
class ToolsGmailActionsTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_gmail_action_preview_returns_plan,
        ]

    def get_suite_name(self) -> str:
        return "Gmail Plugin Actions"

    def get_suite_description(self) -> str:
        return "Integration tests for gmail_digest preview/approval flow and action execution preconditions"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(ToolsGmailActionsTestSuite, globals())
