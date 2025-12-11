import os
import logging

# Ensure rate limiting enabled with sensible defaults. Quotas default to disabled unless set per-tool
os.environ.setdefault("SHU_ENABLE_RATE_LIMITING", "true")
os.environ.setdefault("SHU_RATE_LIMIT_USER_REQUESTS", "100")
os.environ.setdefault("SHU_RATE_LIMIT_USER_PERIOD", "60")
os.environ.setdefault("SHU_PLUGIN_QUOTA_DAILY_REQUESTS_DEFAULT", "0")
os.environ.setdefault("SHU_PLUGIN_QUOTA_MONTHLY_REQUESTS_DEFAULT", "0")

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data
from integ.helpers.auth import create_active_user_headers

logger = logging.getLogger(__name__)


async def _enable_tool(client, auth_headers, name: str):
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        f"/api/v1/plugins/admin/{name}/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_limits_stats_rate_limit_and_quota_smoke(client, db, auth_headers):
    # ----- Rate limiter path (rl:plugin:*) -----
    await _enable_tool(client, auth_headers, name="test_echo")

    # Configure strict per-user rate limit (1 per 60s), quotas disabled
    resp = await client.put(
        "/api/v1/plugins/admin/test_echo/limits",
        json={
            "rate_limit_user_requests": 1,
            "rate_limit_user_period": 60,
            "quota_daily_requests": 0,
            "quota_monthly_requests": 0,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    body = {"params": {"message": "hello"}}
    r1 = await client.post("/api/v1/plugins/test_echo/execute", json=body, headers=auth_headers)
    assert r1.status_code == 200, r1.text
    r2 = await client.post("/api/v1/plugins/test_echo/execute", json=body, headers=auth_headers)
    assert r2.status_code == 429, r2.text

    # Fetch limiter stats
    s = await client.get("/api/v1/plugins/admin/limits/stats", params={"prefix": "rl:plugin:", "limit": 50}, headers=auth_headers)
    assert s.status_code == 200, s.text
    data = extract_data(s)
    assert data["prefix"] == "rl:plugin:", data
    assert isinstance(data.get("entries"), list) and len(data["entries"]) >= 1
    # At least one entry should be a hash with token bucket fields
    assert any(e.get("data_type") in ("hash", "string") for e in data["entries"])  # minimal check

    # ----- Quota path (quota:d:*) -----
    await _enable_tool(client, auth_headers, name="test_schema")
    # Daily quota = 1, RL generous
    resp = await client.put(
        "/api/v1/plugins/admin/test_schema/limits",
        json={
            "quota_daily_requests": 1,
            "rate_limit_user_requests": 100,
            "rate_limit_user_period": 60,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    body2 = {"params": {"q": "hi"}}
    r1q = await client.post("/api/v1/plugins/test_schema/execute", json=body2, headers=auth_headers)
    assert r1q.status_code == 200, r1q.text
    r2q = await client.post("/api/v1/plugins/test_schema/execute", json=body2, headers=auth_headers)
    assert r2q.status_code == 429, r2q.text

    s2 = await client.get("/api/v1/plugins/admin/limits/stats", params={"prefix": "quota:d:", "limit": 50}, headers=auth_headers)
    assert s2.status_code == 200, s2.text
    data2 = extract_data(s2)
    assert data2["prefix"] == "quota:d:", data2
    assert isinstance(data2.get("entries"), list) and len(data2["entries"]) >= 1
    # All keys returned should match prefix
    assert all(e.get("key", "").startswith("quota:d:") for e in data2["entries"])  # smoke assertion


async def test_limits_stats_requires_admin(client, db, auth_headers):
    # Create a non-admin user and attempt to access stats
    user_headers = await create_active_user_headers(client, auth_headers, role="regular_user")
    resp = await client.get("/api/v1/plugins/admin/limits/stats", params={"prefix": "rl:plugin:"}, headers=user_headers)
    assert resp.status_code == 403, resp.text


class AdminLimitsStatsTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_limits_stats_rate_limit_and_quota_smoke,
            test_limits_stats_requires_admin,
        ]

    def get_suite_name(self) -> str:
        return "Admin Limits Stats"

    def get_suite_description(self) -> str:
        return "Minimal admin visibility endpoints for rate limiting/quota stats"


if __name__ == "__main__":
    create_test_runner_script(AdminLimitsStatsTestSuite, globals())

