"""
Integration tests for LLM Provider functionality using custom test framework.

These tests verify actual application workflows end-to-end:
- API endpoints work correctly
- Database operations succeed
- Authentication and authorization work
- Real user scenarios are tested
"""

import sys
import os
from typing import List, Callable
from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data
from integ.expected_error_context import (
    expect_authentication_errors,
    expect_validation_errors,
    expect_duplicate_errors,
    ExpectedErrorContext
)


# Test Data
VALID_PROVIDER_DATA = {
    "name": "Test OpenAI Integration",
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-12345",
    "organization_id": "test-org",
    "is_active": True,
    "supports_streaming": True,
    "supports_functions": True,
    "supports_vision": False,
    "rate_limit_rpm": 3500,
    "rate_limit_tpm": 90000,
    "budget_limit_monthly": 100.0
}


async def test_health_endpoint(client, db, auth_headers):
    """Test that the health endpoint works."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["status"] in ("healthy", "warning")
    assert "timestamp" in data["data"]
    assert "checks" in data["data"]


async def test_list_providers_empty(client, db, auth_headers):
    """Test listing providers when none exist."""
    response = await client.get("/api/v1/llm/providers", headers=auth_headers)
    assert response.status_code == 200
    providers = extract_data(response)
    assert isinstance(providers, list)
    # May not be empty if other providers exist, but should be a list


async def test_create_provider_success(client, db, auth_headers):
    """Test successful provider creation end-to-end."""
    # Create provider via API
    response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )

    assert response.status_code == 201
    provider = extract_data(response)

    # Verify response structure
    assert provider["name"] == VALID_PROVIDER_DATA["name"]
    assert provider["provider_type"] == VALID_PROVIDER_DATA["provider_type"]
    assert provider["is_active"] is True
    assert provider["has_api_key"] is True
    assert "api_key" not in provider  # Security: API key should not be returned
    assert "id" in provider

    # Verify data was actually stored in database
    result = await db.execute(
        text("SELECT name, provider_type, api_key_encrypted FROM llm_providers WHERE name = :name"),
        {"name": VALID_PROVIDER_DATA["name"]}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] == VALID_PROVIDER_DATA["name"]  # name
    assert db_row[1] == VALID_PROVIDER_DATA["provider_type"]  # provider_type
    assert db_row[2] is not None  # api_key_encrypted should exist
    assert db_row[2] != VALID_PROVIDER_DATA["api_key"]  # Should be encrypted, not plain text

    return provider["id"]  # Return for use in other tests


async def test_get_provider_by_id(client, db, auth_headers):
    """Test retrieving a specific provider by ID."""
    # First create a provider
    create_response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    provider_id = extract_data(create_response)["id"]

    # Now retrieve it
    response = await client.get(f"/api/v1/llm/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 200
    
    provider = extract_data(response)
    assert provider["id"] == provider_id
    assert provider["name"] == VALID_PROVIDER_DATA["name"]
    assert provider["has_api_key"] is True
    assert "api_key" not in provider  # Security check


async def test_update_provider(client, db, auth_headers):
    """Test updating a provider."""
    # Create provider first
    create_response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    provider_id = extract_data(create_response)["id"]

    # Update the provider
    update_data = {
        "name": "Updated Test Provider",
        "is_active": False,
        "rate_limit_rpm": 5000,
        "provider_type": VALID_PROVIDER_DATA["provider_type"],
        "api_endpoint": VALID_PROVIDER_DATA["api_endpoint"],
    }
    
    response = await client.put(
        f"/api/v1/llm/providers/{provider_id}",
        json=update_data,
        headers=auth_headers
    )
    assert response.status_code == 200
    
    updated_provider = extract_data(response)
    assert updated_provider["name"] == "Updated Test Provider"
    assert updated_provider["is_active"] is False
    assert updated_provider["rate_limit_rpm"] == 5000
    assert updated_provider["has_api_key"] is True  # Should still have API key
    
    # Verify in database
    result = await db.execute(
        text("SELECT name, is_active, rate_limit_rpm FROM llm_providers WHERE id = :id"),
        {"id": provider_id}
    )
    db_row = result.fetchone()
    assert db_row[0] == "Updated Test Provider"
    assert db_row[1] is False  # is_active
    assert db_row[2] == 5000  # rate_limit_rpm


async def test_delete_provider(client, db, auth_headers):
    """Test deleting a provider."""
    # Create provider first
    create_response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    provider_id = extract_data(create_response)["id"]

    # Delete the provider
    response = await client.delete(f"/api/v1/llm/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 204
    
    # Verify it's gone from database
    result = await db.execute(
        text("SELECT COUNT(*) FROM llm_providers WHERE id = :id"),
        {"id": provider_id}
    )
    count = result.scalar()
    assert count == 0
    
    # Verify 404 when trying to get deleted provider
    get_response = await client.get(f"/api/v1/llm/providers/{provider_id}", headers=auth_headers)
    assert get_response.status_code == 404


async def test_create_provider_duplicate_name(client, db, auth_headers):
    """Test that creating a provider with duplicate name fails."""
    # Create first provider
    response1 = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )
    assert response1.status_code == 201
    
    # Try to create another with same name
    response2 = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,  # Same data, including name
        headers=auth_headers
    )
    assert response2.status_code == 400
    error = response2.json()
    assert "already exists" in error["error"]["message"].lower()


async def test_create_provider_invalid_data(client, db, auth_headers):
    """Test that creating a provider with invalid data fails."""
    # Test missing required fields (should fail Pydantic validation)
    invalid_data = {
        "name": "Test Provider"
        # Missing required fields: provider_type, api_endpoint
    }

    response = await client.post(
        "/api/v1/llm/providers",
        json=invalid_data,
        headers=auth_headers
    )
    assert response.status_code == 422  # Validation error


async def test_unauthorized_access(client, db, auth_headers):
    """Test that endpoints require authentication."""
    # Try to access without auth headers
    response = await client.get("/api/v1/llm/providers")
    assert response.status_code == 401
    
    # Try to create without auth
    response = await client.post("/api/v1/llm/providers", json=VALID_PROVIDER_DATA)
    assert response.status_code == 401


async def test_api_key_security_workflow(client, db, auth_headers):
    """Test complete API key security workflow."""
    # Create provider with API key
    response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )
    assert response.status_code == 201, response.status_code
    provider = extract_data(response)
    provider_id = provider["id"]
    
    # Verify API key is not returned but has_api_key is True
    assert "api_key" not in provider, provider
    assert provider["has_api_key"] is True, provider
    
    # Update provider without changing API key
    update_response = await client.put(
        f"/api/v1/llm/providers/{provider_id}",
        json={"name": "Updated Security Test", "provider_type": "local", "api_endpoint": "some_endpoint"},
        headers=auth_headers
    )
    assert update_response.status_code == 200, update_response
    updated_provider = extract_data(update_response)

    # API key should still be present (has_api_key=True)
    assert updated_provider["has_api_key"] is True, updated_provider
    assert "api_key" not in updated_provider, updated_provider
    
    # Verify in database that encrypted key is still there
    result = await db.execute(
        text("SELECT api_key_encrypted FROM llm_providers WHERE id = :id"),
        {"id": provider_id}
    )
    encrypted_key = result.scalar()
    assert encrypted_key is not None, encrypted_key
    assert encrypted_key != VALID_PROVIDER_DATA["api_key"], encrypted_key


async def test_provider_type_endpoints(client, db, auth_headers):
    """Test the list /provider-types APIs."""
    response = await client.get(
        "/api/v1/llm/provider-types",
        headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json().get("data") is not None
    assert len(response.json().get("data")) > 0
    openai = next(filter(
        lambda x: x["key"] == "openai",
        response.json().get("data", [])
    ), None)
    assert set(openai.keys()) == set(["key", "display_name", "provider_adapter_name", "is_active"])

    response = await client.get(
        "/api/v1/llm/provider-types/openai",
        headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json().get("data") is not None
    assert "provider_adapter_name" in response.json()["data"]
    assert "is_active" in response.json()["data"]
    assert "parameter_mapping" in response.json()["data"]


class LLMProviderTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for LLM Provider functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return all LLM provider test functions."""
        return [
            test_health_endpoint,
            test_list_providers_empty,
            test_create_provider_success,
            test_get_provider_by_id,
            test_update_provider,
            test_delete_provider,
            test_create_provider_duplicate_name,
            test_create_provider_invalid_data,
            test_unauthorized_access,
            test_api_key_security_workflow,
            test_provider_type_endpoints,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "LLM Provider Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for LLM provider CRUD operations, security, and API functionality"

    def get_cli_examples(self) -> str:
        """Return LLM-specific CLI examples."""
        return """
Examples:
  python tests/test_llm_integration.py                           # Run all LLM provider tests
  python tests/test_llm_integration.py --list                    # List available tests
  python tests/test_llm_integration.py --test test_create_provider_success
  python tests/test_llm_integration.py --test test_create_provider_success test_update_provider
  python tests/test_llm_integration.py --pattern create          # Run all 'create' tests
  python tests/test_llm_integration.py --pattern "api_key|security"  # Run security-related tests
  python tests/test_llm_integration.py --pattern "provider"      # Run all provider tests
        """


if __name__ == "__main__":
    suite = LLMProviderTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
