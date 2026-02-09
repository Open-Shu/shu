from __future__ import annotations

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data


async def test_microsoft_status_default(client, db, auth_headers):
    resp = await client.get("/api/v1/host/auth/status", params={"providers": "microsoft"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    st = data.get("microsoft") or {}
    assert st.get("user_connected") in (False, 0), st
    assert isinstance(st.get("granted_scopes"), list)


async def test_microsoft_disconnect_noop(client, db, auth_headers):
    # Should succeed even if not connected
    resp = await client.post("/api/v1/host/auth/disconnect", json={"provider": "microsoft"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data.get("provider") == "microsoft"
    assert bool(data.get("disconnected")) is True


class MicrosoftHostAuthTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_microsoft_status_default,
            test_microsoft_disconnect_noop,
        ]

    def get_suite_name(self) -> str:
        return "Microsoft Host Auth"

    def get_suite_description(self) -> str:
        return "Smoke tests for Microsoft provider host auth endpoints"


if __name__ == "__main__":
    suite = MicrosoftHostAuthTestSuite()
    exit_code = suite.run()
    import sys as _sys

    _sys.exit(exit_code)
