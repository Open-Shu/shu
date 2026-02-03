from sqlalchemy import select, text

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from shu.models import PluginStorage


# --- Test functions ---
async def test_secrets_storage_cache_roundtrip(client, db, auth_headers):
    # Sync and enable test_hostcaps tool
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_hostcaps/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Set a secret via plugin execute (scoped to current user)
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_set", "key": "api_key", "value": "secret-abc"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Read the secret via plugin execute
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_get", "key": "api_key"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Envelope: {data: {status, data: {result: ...}}}
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == "secret-abc", data

    # Storage put/get
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "storage_put", "key": "cursor", "value": {"page": 2}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "storage_get", "key": "cursor"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == {"page": 2}, data

    # Storage put/get with UPDATE (upsert) - verify value is updated, not duplicated
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "storage_put", "key": "cursor", "value": {"page": 5}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "storage_get", "key": "cursor"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == {"page": 5}, f"Expected updated value {{page: 5}}, got {result}"

    # Secret update (upsert) - verify value is updated, not duplicated
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_set", "key": "api_key", "value": "secret-xyz-updated"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_get", "key": "api_key"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == "secret-xyz-updated", f"Expected updated secret 'secret-xyz-updated', got {result}"

    # Cache set/get
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "cache_set", "key": "t1", "value": {"x": 1}, "ttl": 5}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "cache_get", "key": "t1"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    inner = payload.get("data", {}) if isinstance(payload, dict) else {}
    result = inner.get("result")
    assert result == {"x": 1}, data


async def test_plugin_storage_db_isolation(client, db, auth_headers):
    """Verify that plugin storage/secrets are stored in plugin_storage table, not agent_memory."""
    # Sync and enable test_hostcaps tool
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_hostcaps/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Create a unique key for this test
    test_key = "db_isolation_test_key"
    test_value = {"isolation": "verified"}

    # Store via storage capability
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "storage_put", "key": test_key, "value": test_value}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Verify data is in plugin_storage table
    result = await db.execute(
        select(PluginStorage).where(
            PluginStorage.plugin_name == "test_hostcaps",
            PluginStorage.namespace == "storage",
            PluginStorage.key == test_key,
        )
    )
    record = result.scalars().first()
    assert record is not None, "Storage record should exist in plugin_storage table"
    assert record.value == {"json": test_value}, f"Expected {test_value}, got {record.value}"

    # Verify data is NOT in agent_memory
    am_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM agent_memory " "WHERE agent_key = 'tool_storage:test_hostcaps' AND key = :key"
        ).bindparams(key=test_key)
    )
    count = am_result.scalar()
    assert count == 0, "Storage should NOT be in agent_memory table"

    # Test secret storage in plugin_storage
    secret_key = "db_isolation_secret"
    resp = await client.post(
        "/api/v1/plugins/test_hostcaps/execute",
        json={"params": {"op": "secret_set", "key": secret_key, "value": "secret-val"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Verify secret is in plugin_storage with namespace='secret'
    result = await db.execute(
        select(PluginStorage).where(
            PluginStorage.plugin_name == "test_hostcaps",
            PluginStorage.namespace == "secret",
            PluginStorage.key == secret_key,
        )
    )
    secret_record = result.scalars().first()
    assert secret_record is not None, "Secret should exist in plugin_storage table"
    # Secrets are encrypted, so we can't check the exact value

    # Verify secret is NOT in agent_memory
    am_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM agent_memory " "WHERE agent_key = 'tool_secret:test_hostcaps' AND key = :key"
        ).bindparams(key=secret_key)
    )
    count = am_result.scalar()
    assert count == 0, "Secret should NOT be in agent_memory table"


# --- Suite wrapper ---
class HostCapabilitiesTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_secrets_storage_cache_roundtrip,
            test_plugin_storage_db_isolation,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 HostCapabilities"

    def get_suite_description(self) -> str:
        return "Integration tests for secrets, storage, cache capabilities"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(HostCapabilitiesTestSuite, globals())
