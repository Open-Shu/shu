"""
API Key Authentication Integration Tests for Shu

These tests verify the global API key authentication (Tier 0) that allows
external clients to authenticate via `Authorization: ApiKey <key>` header.

This tests the middleware authentication flow in middleware.py that:
1. Accepts `Authorization: ApiKey <key>` header
2. Validates against SHU_API_KEY environment variable
3. Maps requests to user via SHU_API_KEY_USER_EMAIL
"""

import sys
import os
import logging
import uuid
from typing import List, Callable
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base_integration_test import BaseIntegrationTestSuite
from tests.response_utils import extract_data

logger = logging.getLogger(__name__)


# Test API key value used in tests
TEST_API_KEY = f"test-api-key-{uuid.uuid4().hex[:16]}"


async def test_api_key_auth_success(client, db, auth_headers):
    """Test that valid API key authentication works end-to-end."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    # Get the admin user email from auth_headers
    # The auth_headers fixture uses an admin user - we'll use that email for mapping
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    user_data = extract_data(response)
    admin_email = user_data["email"]

    # Patch settings to configure API key auth
    with patch.object(settings, 'api_key', TEST_API_KEY), \
         patch.object(settings, 'api_key_user_email', admin_email):

        # Make request with API key header
        api_key_headers = {"Authorization": f"ApiKey {TEST_API_KEY}"}
        response = await client.get("/api/v1/llm/providers", headers=api_key_headers)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert "data" in data, "Response should have 'data' envelope"


async def test_api_key_auth_invalid_key(client, db, auth_headers):
    """Test that invalid API key is rejected."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    with patch.object(settings, 'api_key', TEST_API_KEY), \
         patch.object(settings, 'api_key_user_email', "test@example.com"):

        # Make request with wrong API key
        api_key_headers = {"Authorization": "ApiKey wrong-key-value"}
        response = await client.get("/api/v1/llm/providers", headers=api_key_headers)

        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        data = response.json()
        assert "detail" in data


async def test_api_key_auth_no_key_configured(client, db, auth_headers):
    """Test that API key auth fails when no key is configured."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    with patch.object(settings, 'api_key', None), \
         patch.object(settings, 'api_key_user_email', "test@example.com"):

        api_key_headers = {"Authorization": f"ApiKey {TEST_API_KEY}"}
        response = await client.get("/api/v1/llm/providers", headers=api_key_headers)

        assert response.status_code == 401, f"Expected 401 when no API key configured"


async def test_api_key_auth_no_user_mapping(client, db, auth_headers):
    """Test that API key auth fails when user mapping is not configured."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    with patch.object(settings, 'api_key', TEST_API_KEY), \
         patch.object(settings, 'api_key_user_email', None):

        api_key_headers = {"Authorization": f"ApiKey {TEST_API_KEY}"}
        response = await client.get("/api/v1/llm/providers", headers=api_key_headers)

        assert response.status_code == 401, f"Expected 401 when user mapping not configured"
        data = response.json()
        assert "API key user mapping not configured" in data.get("detail", "")


async def test_api_key_auth_user_not_found(client, db, auth_headers):
    """Test that API key auth fails when mapped user doesn't exist."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    with patch.object(settings, 'api_key', TEST_API_KEY), \
         patch.object(settings, 'api_key_user_email', "nonexistent@example.com"):

        api_key_headers = {"Authorization": f"ApiKey {TEST_API_KEY}"}
        response = await client.get("/api/v1/llm/providers", headers=api_key_headers)

        assert response.status_code == 401, f"Expected 401 when mapped user doesn't exist"


async def test_api_key_auth_creates_conversation(client, db, auth_headers):
    """Test that API key auth can create resources (not just read)."""
    from shu.core.config import get_settings_instance
    settings = get_settings_instance()

    # Get admin user email
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    admin_email = extract_data(response)["email"]

    # Get an available model configuration for conversation creation
    response = await client.get("/api/v1/model-configurations", headers=auth_headers)
    assert response.status_code == 200
    configs_data = extract_data(response)
    assert len(configs_data["items"]) > 0, "No model configurations available for test"
    model_config_id = configs_data["items"][0]["id"]

    with patch.object(settings, 'api_key', TEST_API_KEY), \
         patch.object(settings, 'api_key_user_email', admin_email):

        api_key_headers = {"Authorization": f"ApiKey {TEST_API_KEY}"}

        # Create a conversation via API key auth
        response = await client.post(
            "/api/v1/chat/conversations",
            json={
                "title": "API Key Test Conversation",
                "model_configuration_id": model_config_id,
            },
            headers=api_key_headers
        )

        # Note: The API returns 200 for conversation creation (not 201)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = extract_data(response)
        assert "id" in data

        # Clean up - delete the conversation
        conv_id = data["id"]
        await client.delete(f"/api/v1/chat/conversations/{conv_id}", headers=auth_headers)


class ApiKeyAuthTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for API Key Authentication."""

    def get_test_functions(self) -> List[Callable]:
        return [
            test_api_key_auth_success,
            test_api_key_auth_invalid_key,
            test_api_key_auth_no_key_configured,
            test_api_key_auth_no_user_mapping,
            test_api_key_auth_user_not_found,
            test_api_key_auth_creates_conversation,
        ]

    def get_suite_name(self) -> str:
        return "API Key Authentication Integration Tests"

    def get_suite_description(self) -> str:
        return "Tests for Authorization: ApiKey <key> header authentication (Tier 0)"


if __name__ == "__main__":
    suite = ApiKeyAuthTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)

