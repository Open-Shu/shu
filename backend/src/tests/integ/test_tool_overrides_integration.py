import os
import logging

# Ensure rate limiting is enabled but with lenient globals so override effects are visible
os.environ.setdefault("SHU_ENABLE_API_RATE_LIMITING", "true")
os.environ["SHU_RATE_LIMIT_USER_REQUESTS"] = "100"
os.environ["SHU_RATE_LIMIT_USER_PERIOD"] = "60"
os.environ.setdefault("SHU_PLUGIN_QUOTA_DAILY_REQUESTS_DEFAULT", "0")
os.environ.setdefault("SHU_PLUGIN_QUOTA_MONTHLY_REQUESTS_DEFAULT", "0")

# Apply Alembic migrations to ensure new columns exist
try:
    from alembic.config import Config
    from alembic import command
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
except Exception as _e:
    # Tests may still pass if DB already has required schema; log and continue
    logging.getLogger(__name__).warning(f"Alembic upgrade attempt failed or skipped: {_e}")

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script  # noqa: E402

logger = logging.getLogger(__name__)


async def _enable_tool(client, auth_headers, name: str = "test_echo"):
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        f"/api/v1/plugins/admin/{name}/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


# --- Tests ---
async def test_per_tool_rate_limit_override_headers(client, db, auth_headers):
    await _enable_tool(client, auth_headers, name="test_echo")

    # Snapshot existing limits to restore after test
    prev_resp = await client.get("/api/v1/plugins/admin/test_echo/limits", headers=auth_headers)
    assert prev_resp.status_code == 200, prev_resp.text
    prev_limits = prev_resp.json().get("data", {}).get("limits", {}) or {}

    try:
        # Set override: 1 request per 60s, disable quotas to avoid interference
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
        # Debug output for headers
        logger.info(f"Rate limit 429 headers: {dict(r2.headers)}")
        # Check headers
        assert r2.headers.get("Retry-After") is not None
        assert r2.headers.get("RateLimit-Limit") == "1;w=60"
        assert r2.headers.get("RateLimit-Remaining") == "0"
        assert r2.headers.get("RateLimit-Reset") is not None
    finally:
        # Best-effort restore of previous limits; note: API does not support clearing keys explicitly
        restore_payload = {}
        for k in ("rate_limit_user_requests", "rate_limit_user_period", "quota_daily_requests", "quota_monthly_requests"):
            if k in prev_limits:
                restore_payload[k] = prev_limits[k]
        if restore_payload:
            _ = await client.put(
                "/api/v1/plugins/admin/test_echo/limits",
                json=restore_payload,
                headers=auth_headers,
            )


async def test_per_tool_quota_override_headers(client, db, auth_headers):
    # Use a different tool to avoid Redis counter collisions with previous tests
    await _enable_tool(client, auth_headers, name="test_schema")

    # Snapshot existing limits to restore after test
    prev_resp = await client.get("/api/v1/plugins/admin/test_schema/limits", headers=auth_headers)
    assert prev_resp.status_code == 200, prev_resp.text
    prev_limits = prev_resp.json().get("data", {}).get("limits", {}) or {}

    try:
        # Set override on test_schema: daily quota = 1; keep RL lenient to avoid RL interference
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

        body = {"params": {"q": "hello"}}
        r1 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
        if r1.status_code != 200:
            logger.info(f"First call unexpected status={r1.status_code} headers={dict(r1.headers)} body={r1.text}")
        assert r1.status_code == 200, r1.text

        r2 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
        logger.info(f"Quota 429 headers: {dict(r2.headers)} body={r2.text}")
        assert r2.status_code == 429, r2.text
        # Check headers for quota
        assert r2.headers.get("Retry-After") is not None
        # Expect daily window w=86400
        assert r2.headers.get("RateLimit-Limit") == "1;w=86400"
        assert r2.headers.get("RateLimit-Remaining") == "0"
        assert r2.headers.get("RateLimit-Reset") is not None
    finally:
        # Best-effort restore of previous limits
        restore_payload = {}
        for k in ("rate_limit_user_requests", "rate_limit_user_period", "quota_daily_requests", "quota_monthly_requests"):
            if k in prev_limits:
                restore_payload[k] = prev_limits[k]
        if restore_payload:
            _ = await client.put(
                "/api/v1/plugins/admin/test_schema/limits",
                json=restore_payload,
                headers=auth_headers,
            )


# --- Suite wrapper ---
class PluginOverridesTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_per_tool_rate_limit_override_headers,
            test_per_tool_quota_override_headers,
        ]

    def get_suite_name(self) -> str:
        return "Plugins v1 Per-Plugin Overrides"

    def get_suite_description(self) -> str:
        return "Integration tests for per-plugin rate limit & quota overrides and 429 headers"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(PluginOverridesTestSuite, globals())

