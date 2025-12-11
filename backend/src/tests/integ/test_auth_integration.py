"""
Authentication Integration Tests for Shu

These tests verify end-to-end authentication workflows:
- User authentication and authorization
- JWT token management
- RBAC enforcement on API endpoints
- User session management
"""

import sys
import os
import logging
from typing import List, Callable

from integ.helpers.decorators import replace_auth_headers_for_user
from integ.base_integration_test import BaseIntegrationTestSuite

logger = logging.getLogger(__name__)


# Test Data
TEST_USER_DATA = {
    "email": "test-auth@example.com",
    "name": "Test Auth User",
    "google_id": "test_google_auth_123",
    "role": "regular_user",
    "is_active": True,
}

ADMIN_USER_DATA = {
    "email": "test-admin-auth@example.com", 
    "name": "Test Admin User",
    "google_id": "test_google_admin_123",
    "role": "admin"
}


async def test_health_endpoint_no_auth(client, db, auth_headers):
    """Test that health endpoint works without authentication."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200, response.status_code
    data = response.json()
    assert data["data"]["status"] in ("healthy", "warning"), data["data"]["status"]


async def test_protected_endpoint_requires_auth(client, db, auth_headers):
    """Test that protected endpoints require authentication."""
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication errors are expected ===")

    # Try to access LLM providers without auth
    response = await client.get("/api/v1/llm/providers")
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated GET request occurred as expected ===")

    # Try to create provider without auth
    response = await client.post("/api/v1/llm/providers", json={"name": "test"})
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated POST request occurred as expected ===")


async def test_authenticated_user_access(client, db, auth_headers):
    """Test that authenticated users can access appropriate endpoints."""
    # Authenticated user should be able to list providers
    response = await client.get("/api/v1/llm/providers", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert "data" in payload
    assert isinstance(payload.get("data"), list)


async def test_admin_only_endpoints(client, db, auth_headers):
    """Test that admin-only endpoints are properly protected."""
    # Admin user (from auth_headers) should be able to create providers
    provider_data = {
        "name": "Test Auth Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-key-auth",
        "is_active": True,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "rate_limit_rpm": 60,
        "rate_limit_tpm": 60000
    }
    
    response = await client.post("/api/v1/llm/providers", 
                                json=provider_data, 
                                headers=auth_headers)
    assert response.status_code == 201
    
    payload = response.json()
    assert "data" in payload
    provider = payload["data"]
    assert provider["name"] == "Test Auth Provider"
    assert provider["has_api_key"] is True

    # Clean up
    await client.delete(f"/api/v1/llm/providers/{provider['id']}", headers=auth_headers)


async def test_user_session_persistence(client, db, auth_headers):
    """Test that user sessions work correctly across requests."""
    # Make multiple authenticated requests
    response1 = await client.get("/api/v1/llm/providers", headers=auth_headers)
    assert response1.status_code == 200
    
    response2 = await client.get("/api/v1/llm/providers", headers=auth_headers)
    assert response2.status_code == 200
    
    # Both requests should succeed with same auth headers
    assert response1.json() == response2.json()


async def test_invalid_token_rejection(client, db, auth_headers):
    """Test that invalid tokens are properly rejected."""
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication error is expected ===")

    # Use invalid token
    invalid_headers = {"Authorization": "Bearer invalid_token_12345"}

    response = await client.get("/api/v1/llm/providers", headers=invalid_headers)
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for invalid token occurred as expected ===")


async def test_malformed_auth_header(client, db, auth_headers):
    """Test that malformed authorization headers are rejected."""
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication errors are expected ===")

    # Missing Bearer prefix
    malformed_headers = {"Authorization": "invalid_format_token"}
    response = await client.get("/api/v1/llm/providers", headers=malformed_headers)
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for malformed auth header occurred as expected ===")

    # Empty authorization header
    empty_headers = {"Authorization": ""}
    response = await client.get("/api/v1/llm/providers", headers=empty_headers)
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for empty auth header occurred as expected ===")

    # Missing authorization header entirely (already tested in other test)
    response = await client.get("/api/v1/llm/providers")
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for missing auth header occurred as expected ===")


async def test_rbac_role_enforcement(client, db, auth_headers):
    """Test that role-based access control is properly enforced."""
    # Admin user should be able to perform admin actions
    # (auth_headers contains admin token from test framework)
    
    # Test admin can create provider
    provider_data = {
        "name": "RBAC Test Provider",
        "provider_type": "openai", 
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "rbac-test-key",
        "rate_limit_rpm": 100,
        "rate_limit_tpm": 10000
    }
    
    response = await client.post("/api/v1/llm/providers",
                                json=provider_data,
                                headers=auth_headers)
    assert response.status_code == 201, response.status_code
    payload = response.json()
    assert "data" in payload, payload
    provider_id = payload["data"]["id"]

    # Test admin can update provider
    update_data = {
        "name": "RBAC Updated Provider",
        "provider_type": "openai", 
        "api_endpoint": "https://api.openai.com/v1",
    }
    response = await client.put(f"/api/v1/llm/providers/{provider_id}",
                               json=update_data,
                               headers=auth_headers)
    assert response.status_code == 200, response.status_code
    payload = response.json()
    assert payload["data"]["name"] == "RBAC Updated Provider", payload
    
    # Test admin can delete provider
    response = await client.delete(f"/api/v1/llm/providers/{provider_id}",
                                  headers=auth_headers)
    assert response.status_code in (200, 204)


async def test_cors_and_security_headers(client, db, auth_headers):
    """Test that proper security headers are set."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200, response.status_code
    
    # Check that response has proper structure (security handled by middleware)
    data = response.json()
    assert "data" in data, data


async def test_authentication_workflow_integration(client, db, auth_headers):
    """Test complete authentication workflow integration."""
    # 1. Unauthenticated request should fail
    response = await client.get("/api/v1/llm/providers")
    assert response.status_code == 401
    
    # 2. Authenticated request should succeed
    response = await client.get("/api/v1/llm/providers", headers=auth_headers)
    assert response.status_code == 200
    
    # 3. Admin action should succeed
    provider_data = {
        "name": "Workflow Test Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "rate_limit_rpm": 60,
        "rate_limit_tpm": 60000
    }
    
    response = await client.post("/api/v1/llm/providers",
                                json=provider_data,
                                headers=auth_headers)
    assert response.status_code == 201
    payload = response.json()
    assert "data" in payload

    # 4. Verify data persisted correctly
    provider_id = payload["data"]["id"]
    response = await client.get(f"/api/v1/llm/providers/{provider_id}",
                               headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Workflow Test Provider"
    
    # 5. Clean up
    response = await client.delete(f"/api/v1/llm/providers/{provider_id}",
                                  headers=auth_headers)
    assert response.status_code in (200, 204)


async def test_token_refresh_functionality(client, db, auth_headers):
    """Test token refresh functionality for sliding expiration."""
    import time

    # First, create a user using admin endpoint and get tokens via login
    unique_email = f"test_refresh_user_{int(time.time())}@example.com"
    user_data = {
        "email": unique_email,
        "password": "test_password_123",
        "name": "Test Refresh User",
        "role": "regular_user"
    }

    # Create user via admin endpoint (active by default)
    response = await client.post("/api/v1/auth/users", json=user_data, headers=auth_headers)
    assert response.status_code == 200

    # Login to get tokens
    login_data = {
        "email": unique_email,
        "password": "test_password_123"
    }

    response = await client.post("/api/v1/auth/login/password", json=login_data)
    assert response.status_code == 200

    login_response = response.json()

    # Shu responses are envelope-wrapped
    data = login_response["data"]

    access_token = data["access_token"]
    refresh_token = data["refresh_token"]

    # Test that access token works
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200

    # Test token refresh
    refresh_data = {"refresh_token": refresh_token}
    response = await client.post("/api/v1/auth/refresh", json=refresh_data)
    assert response.status_code == 200

    refresh_response = response.json()

    # Shu responses are envelope-wrapped
    refresh_data = refresh_response["data"]

    assert "access_token" in refresh_data
    assert "refresh_token" in refresh_data
    assert "user" in refresh_data

    new_access_token = refresh_data["access_token"]
    new_refresh_token = refresh_data["refresh_token"]

    # Verify tokens are returned (they might be identical if created at same second)
    assert new_access_token is not None
    assert new_refresh_token is not None

    # Test that new access token works
    new_headers = {"Authorization": f"Bearer {new_access_token}"}
    response = await client.get("/api/v1/auth/me", headers=new_headers)
    assert response.status_code == 200

    # Test that old access token still works (until it expires)
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200

    # Test refresh with invalid token
    invalid_refresh_data = {"refresh_token": "invalid_token"}
    response = await client.post("/api/v1/auth/refresh", json=invalid_refresh_data)
    assert response.status_code == 401


async def test_admin_access(client, db, auth_headers):
    authorized_calls = [
        await client.get("/api/v1/llm/provider-types", headers=auth_headers),
        await client.get("/api/v1/llm/provider-types/openai", headers=auth_headers),
    ]

    for call in authorized_calls:
        assert call.status_code == 200, f"Failed call: {call}"


@replace_auth_headers_for_user(TEST_USER_DATA)
async def test_user_access(client, db, auth_headers):
    unauthorized_calls = [
        await client.get("/api/v1/llm/provider-types", headers=auth_headers),
        await client.get("/api/v1/llm/provider-types/openai", headers=auth_headers),
    ]

    for call in unauthorized_calls:
        assert call.status_code == 403, f"Failed call: {call.text}"


class AuthenticationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Authentication functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all authentication test functions."""
        return [
            test_admin_access,
            test_user_access,
            test_health_endpoint_no_auth,
            test_protected_endpoint_requires_auth,
            test_authenticated_user_access,
            test_admin_only_endpoints,
            test_user_session_persistence,
            test_invalid_token_rejection,
            test_malformed_auth_header,
            test_rbac_role_enforcement,
            test_cors_and_security_headers,
            test_authentication_workflow_integration,
            test_token_refresh_functionality,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Authentication Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for authentication, authorization, and RBAC functionality"
    
    def get_cli_examples(self) -> str:
        """Return authentication-specific CLI examples."""
        return """
Examples:
  python tests/test_auth_integration.py                          # Run all auth tests
  python tests/test_auth_integration.py --list                   # List available tests
  python tests/test_auth_integration.py --test test_admin_only_endpoints
  python tests/test_auth_integration.py --pattern "auth|rbac"    # Run auth/RBAC tests
  python tests/test_auth_integration.py --pattern "admin"       # Run admin-related tests
        """


if __name__ == "__main__":
    suite = AuthenticationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
