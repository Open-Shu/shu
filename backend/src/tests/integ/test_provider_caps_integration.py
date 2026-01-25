import asyncio
from contextlib import contextmanager

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script


@contextmanager
def _enable_rate_limiting():
    """
    Temporarily enable rate limiting on the global EXECUTOR.

    Modifies the existing EXECUTOR instance in-place by setting its _limiter
    and _provider_limiter attributes. This is necessary because API routes
    import EXECUTOR at module load time and hold a direct reference to the
    original object.
    """
    from shu.plugins.executor import EXECUTOR
    from shu.core.rate_limiting import TokenBucketRateLimiter

    # Save original limiters
    old_limiter = EXECUTOR._limiter
    old_provider_limiter = EXECUTOR._provider_limiter

    # Create and install new rate limiters
    # High default capacity - per-plugin limits set via API will override
    EXECUTOR._limiter = TokenBucketRateLimiter(
        namespace="rl:plugin:user",
        capacity=100,
        refill_per_second=2,
    )
    EXECUTOR._provider_limiter = TokenBucketRateLimiter(
        namespace="rl:plugin:prov",
        capacity=100,
        refill_per_second=2,
    )
    try:
        yield
    finally:
        # Restore original limiters
        EXECUTOR._limiter = old_limiter
        EXECUTOR._provider_limiter = old_provider_limiter


# --- Test functions ---
async def test_provider_rpm_cap_429(client, db, auth_headers):

    # Sync and enable test_schema
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Set provider RPM cap via per-tool limits and disable quotas so RPM is the only limiter
    resp = await client.put(
        "/api/v1/plugins/admin/test_schema/limits",
        json={
            "provider_name": "prov:test",
            "provider_rpm": 1,
            "provider_window_seconds": 60,
            "quota_daily_requests": 0,
            "quota_monthly_requests": 0,
            "rate_limit_user_requests": 100,
            "rate_limit_user_period": 60,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    body = {"params": {"q": "hello"}}

    # Enable rate limiting for this test via DI
    with _enable_rate_limiting():
        # First request should pass
        r1 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
        assert r1.status_code == 200, r1.text

        # Second request should be provider-rate-limited
        r2 = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
        assert r2.status_code == 429, r2.text
        data = r2.json()
        # Error envelope: {error: {code, message: {error: ..., provider: ..., retry_after: ...}}}
        err = data.get("error") if isinstance(data, dict) else None
        assert isinstance(err, dict), data
        msg = err.get("message") if isinstance(err, dict) else None
        assert isinstance(msg, dict) and msg.get("error") == "provider_rate_limited", data


async def test_provider_concurrency_cap_429(client, db, auth_headers):
    # Sync and enable test_slow
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_slow/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Set provider concurrency cap
    resp = await client.put(
        "/api/v1/plugins/admin/test_slow/limits",
        json={
            "provider_name": "prov:slow",
            "provider_concurrency": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    body = {"params": {"sleep_seconds": 1.0}}

    # Enable rate limiting for this test via DI
    with _enable_rate_limiting():
        # Fire two requests concurrently; one should 429
        t1 = asyncio.create_task(client.post("/api/v1/plugins/test_slow/execute", json=body, headers=auth_headers))
        await asyncio.sleep(0.05)  # allow first to acquire slot
        t2 = asyncio.create_task(client.post("/api/v1/plugins/test_slow/execute", json=body, headers=auth_headers))

        r1, r2 = await asyncio.gather(t1, t2)
        codes = sorted([r1.status_code, r2.status_code])
        assert codes == [200, 429], (r1.text, r2.text)


# --- Suite wrapper ---
class ProviderCapsTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_provider_rpm_cap_429,
            test_provider_concurrency_cap_429,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 Provider Caps"

    def get_suite_description(self) -> str:
        return "Integration tests for provider-level RPM and concurrency caps"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(ProviderCapsTestSuite, globals())
