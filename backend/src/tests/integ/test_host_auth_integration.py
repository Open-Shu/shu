"""
Host Auth Integration Tests

Verifies the generic provider-agnostic delegation check endpoint.
"""

import logging
import sys
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite

logger = logging.getLogger(__name__)


async def test_delegation_check_unsupported_provider(client, db, auth_headers):
    """Unsupported providers should return 400 with error envelope."""
    payload = {
        "provider": "dropbox",
        "subject": "user@example.com",
        "scopes": ["files.metadata.read"],
    }
    response = await client.post("/api/v1/host/auth/delegation-check", json=payload, headers=auth_headers)
    assert response.status_code == 400
    body = response.json()
    assert "error" in body and body["error"].get("message")


async def test_delegation_check_google_returns_payload(client, db, auth_headers):
    """Google provider returns a payload regardless of readiness; assert shape."""
    payload = {
        "provider": "google",
        "subject": "user@example.com",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }
    response = await client.post("/api/v1/host/auth/delegation-check", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json().get("data", {})
    assert isinstance(data, dict)
    # minimal shape checks (ready may be False if not configured)
    assert "ready" in data
    assert isinstance(data.get("ready"), bool)
    if "status" in data:
        assert isinstance(data["status"], int)


class HostAuthTestSuite(BaseIntegrationTestSuite):
    """Integration tests for Host Auth generic provider endpoints."""

    def get_test_functions(self) -> list[Callable]:
        return [
            test_delegation_check_unsupported_provider,
            test_delegation_check_google_returns_payload,
        ]

    def get_suite_name(self) -> str:
        return "Host Auth Integration Tests"

    def get_suite_description(self) -> str:
        return "Tests for /api/v1/host/auth generic endpoints (delegation-check)"


if __name__ == "__main__":
    suite = HostAuthTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
