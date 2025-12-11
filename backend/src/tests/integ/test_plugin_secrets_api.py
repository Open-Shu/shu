from __future__ import annotations

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data


async def _enable_test_hostcaps(client, auth_headers):
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        "/api/v1/plugins/admin/test_hostcaps/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_plugin_secrets_scopes_and_fallback(client, db, auth_headers):
    """Admin + self secrets APIs with system/user scopes and host.secrets fallback.

    This test verifies:
    - Admin can set system-scoped secrets via scope param
    - User can manage their own secrets under /plugins/self
    - host.secrets.get performs user->system fallback
    - User secrets override system secrets when both are present
    """
    # db is unused but required by test runner signature

    await _enable_test_hostcaps(client, auth_headers)

    # Resolve current user id (auth/me returns user_id, not id)
    me_resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me_resp.status_code == 200, me_resp.text
    me = extract_data(me_resp)
    user_id = me["user_id"]

    # 1) Admin sets a system-scoped secret
    system_value = "system-secret-123"
    resp = await client.put(
        "/api/v1/plugins/admin/test_hostcaps/secrets/shared_api_key",
        json={"value": system_value, "scope": "system"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Admin can list system-scoped secrets
    resp = await client.get(
        "/api/v1/plugins/admin/test_hostcaps/secrets",
        params={"scope": "system", "include_meta": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data["scope"] == "system"
    assert "shared_api_key" in data["keys"], data

    # 2) Plugin host.secrets.get falls back to system secret when user secret missing
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_get", "key": "shared_api_key"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json().get("data", {})
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == system_value, f"Expected system-scoped secret value, got {result!r}"

    # 3) User sets an override via /plugins/self
    user_value = "user-secret-override"
    resp = await client.put(
        "/api/v1/plugins/self/test_hostcaps/secrets/shared_api_key",
        json={"value": user_value},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Self listing shows user-scoped secret
    resp = await client.get(
        "/api/v1/plugins/self/test_hostcaps/secrets",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data["scope"] == "user"
    assert data["user_id"] == user_id
    assert "shared_api_key" in data["keys"], data

    # Admin listing for user scope can see the same key
    resp = await client.get(
        "/api/v1/plugins/admin/test_hostcaps/secrets",
        params={"user_id": user_id, "scope": "user"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data["scope"] == "user"
    assert "shared_api_key" in data["keys"], data

    # 4) Plugin now returns user override instead of system value
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_get", "key": "shared_api_key"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json().get("data", {})
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == user_value, f"Expected user-scoped secret override, got {result!r}"


class PluginSecretsApiTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_plugin_secrets_scopes_and_fallback,
        ]

    def get_suite_name(self) -> str:
        return "Plugin Secrets API and host.secrets integration"

    def get_suite_description(self) -> str:
        return "Tests for admin/user plugin secrets APIs and scoped secret behavior"


if __name__ == "__main__":
    create_test_runner_script(PluginSecretsApiTestSuite, globals())

