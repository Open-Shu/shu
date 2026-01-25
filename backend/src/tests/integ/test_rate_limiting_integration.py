import os
import logging

# Set env BEFORE importing test runner/app so settings pick up test overrides
os.environ.setdefault("SHU_ENABLE_API_RATE_LIMITING", "true")
os.environ["SHU_API_RATE_LIMIT_USER_REQUESTS"] = "2"  # 2 requests per period
os.environ["SHU_API_RATE_LIMIT_USER_PERIOD"] = "60"   # per 60 seconds

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script  # noqa: E402

logger = logging.getLogger(__name__)


# --- Test functions ---
async def test_tool_per_user_rate_limit_429(client, db, auth_headers):
    # Sync and enable test_schema tool (use a clean plugin for baseline RL test)
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Ensure per-tool limits align with the env for this test (test isolation)
    # Also explicitly disable any provider-level caps that may have been set by other tests
    resp = await client.put(
        "/api/v1/plugins/admin/test_schema/limits",
        json={
            "rate_limit_user_requests": 2,
            "rate_limit_user_period": 60,
            "quota_daily_requests": 0,
            "quota_monthly_requests": 0,
            "provider_rpm": 0,
            "provider_concurrency": 0,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    body = {"params": {"q": "hello"}}

    # First two requests should pass
    r1 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
    assert r1.status_code == 200, r1.text

    r2 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
    assert r2.status_code == 200, r2.text

    # Third request should be rate limited (per user per tool)
    r3 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
    assert r3.status_code == 429, r3.text
    data = r3.json()
    # Expect FastAPI HTTPException detail or our envelope
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        assert detail.get("error") == "rate_limited"
        assert "retry_after" in detail
    else:
        # Some middleware may wrap as {error:{code:...}}
        err = data.get("error") if isinstance(data, dict) else None
        assert err is not None, f"unexpected body: {data}"


# --- Suite wrapper ---
class RateLimitingTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_tool_per_user_rate_limit_429,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 Rate Limiting"

    def get_suite_description(self) -> str:
        return "Integration tests for per-tool/per-user rate limiting guardrails"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(RateLimitingTestSuite, globals())

