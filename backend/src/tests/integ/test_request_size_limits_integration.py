"""
Integration tests for request/response size guardrails on tool execution endpoints.

Covers:
- 413 on oversized input payloads
- 413 on oversized output payloads
"""

import logging

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


# --- Test functions ---
async def test_execute_input_size_limit_413(client, db, auth_headers):
    # Sync and enable debug_echo tool
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        "/api/v1/plugins/admin/test_schema/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Create a big message to exceed default input cap (default 256 KiB)
    big_message = "A" * (300 * 1024)
    body = {"params": {"q": big_message}}

    resp = await client.post("/api/v1/plugins/test_schema/execute", json=body, headers=auth_headers)
    assert resp.status_code == 413, resp.text
    data = resp.json()
    # Allow either Shu error envelope or FastAPI detail
    if "detail" in data and isinstance(data["detail"], dict):
        detail = data["detail"]
        assert detail.get("error") == "input_too_large"
        assert "limit" in detail and "size" in detail
    elif "error" in data and isinstance(data["error"], dict):
        err = data["error"]
        # Expect a code field like HTTP_413
        assert "413" in str(err.get("code", ""))
    else:
        # Fallback: body must not be a success envelope
        assert False, f"unexpected error shape: {data}"


async def test_execute_output_size_limit_413(client, db, auth_headers):
    # Sync and enable output_bloat tool
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        "/api/v1/plugins/admin/test_output_bloat/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Ask for a blob that exceeds the default output cap (default 1 MiB)
    big_size = 2 * 1024 * 1024
    body = {"params": {"size": big_size, "char": "B"}}

    resp = await client.post("/api/v1/plugins/test_output_bloat/execute", json=body, headers=auth_headers)
    assert resp.status_code == 413, resp.text
    data = resp.json()
    if "detail" in data:
        detail = data["detail"]
        assert detail.get("error") == "output_too_large"
        assert "limit" in detail and "size" in detail


# --- Suite wrapper ---
class RequestSizeLimitsTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_execute_input_size_limit_413,
            test_execute_output_size_limit_413,
        ]

    def get_suite_name(self) -> str:
        return "Tools v1 Size Guardrails"

    def get_suite_description(self) -> str:
        return "Integration tests for request and response size limits on tool execution"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(RequestSizeLimitsTestSuite, globals())
