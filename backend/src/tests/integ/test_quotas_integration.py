import os
import logging

# Set env BEFORE importing test runner/app so settings pick up test overrides
os.environ.setdefault("SHU_ENABLE_RATE_LIMITING", "true")  # rate limiter shouldn't interfere at these counts
os.environ["SHU_PLUGIN_QUOTA_DAILY_REQUESTS_DEFAULT"] = "2"  # allow 2 per day
os.environ["SHU_PLUGIN_QUOTA_MONTHLY_REQUESTS_DEFAULT"] = "0"  # disable monthly for this test

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script  # noqa: E402

logger = logging.getLogger(__name__)


# --- Test functions ---
async def test_tool_daily_quota_429(client, db, auth_headers):
    # Sync and enable test_echo plugin
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_echo/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # set plugin limits
    override_payload = {
        "quota_daily_requests": 2,
        "quota_monthly_requests": 0,
        "rate_limit_user_requests": 100,
        "rate_limit_user_period": 60,
    }
    set_resp = await client.put(
        "/api/v1/plugins/admin/test_echo/limits",
        json=override_payload,
        headers=auth_headers,
    )
    assert set_resp.status_code == 200, set_resp.text

    body = {"params": {"message": "hello"}}
    # First two requests should pass under daily quota=2
    r1 = await client.post("/api/v1/plugins/test_echo/execute", json=body, headers=auth_headers)
    assert r1.status_code == 200, r1.text

    r2 = await client.post("/api/v1/plugins/test_echo/execute", json=body, headers=auth_headers)
    assert r2.status_code == 200, r2.text

    # Third request should hit daily quota and be rejected
    r3 = await client.post("/api/v1/plugins/test_echo/execute", json=body, headers=auth_headers)
    assert r3.status_code == 429, r3.text
    data = r3.json()
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        assert detail.get("error") == "quota_exceeded", data
        assert detail.get("period") == "daily", data
        assert isinstance(detail.get("reset_in"), int)
    else:
        # Some middleware may wrap differently; accept {error:{code:...}}
        err = data.get("error") if isinstance(data, dict) else None
        assert err is not None, f"unexpected body: {data}"


# --- Suite wrapper ---
class QuotasTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_tool_daily_quota_429,
        ]

    def get_suite_name(self) -> str:
        return "Plugins v1 Quotas"

    def get_suite_description(self) -> str:
        return "Integration tests for per-plugin/per-user quota guardrails"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(QuotasTestSuite, globals())
