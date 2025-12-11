from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script


# --- Test functions ---
async def test_undeclared_capability_access_denied(client, db, auth_headers):
    # Sync and enable test_capdeny
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_capdeny/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Execute operation that attempts to use host.secrets without declaring it
    resp = await client.post(
        "/api/v1/plugins/test_capdeny/execute",
        json={"params": {"op": "try_secrets"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    status = payload.get("status") if isinstance(payload, dict) else None
    err = payload.get("error") if isinstance(payload, dict) else None
    # We expect the tool to return an error with a clear message from Host capability guard
    assert status == "error", data
    assert isinstance(err, dict) and "Host capability 'secrets' not declared" in str(err.get("message")), data


# --- Suite wrapper ---
class CapabilityEnforcementTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_undeclared_capability_access_denied,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 Capability Enforcement"

    def get_suite_description(self) -> str:
        return "Negative tests ensure undeclared capabilities are denied with a clear error"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(CapabilityEnforcementTestSuite, globals())

