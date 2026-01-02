"""
Configuration Integration Tests for Shu

These tests verify configuration system functionality:
- Public configuration endpoint accessibility
- Configuration value validation
- Environment variable handling
- Application settings consistency
"""

import sys
import os
from typing import List, Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data


async def test_public_config_endpoint_accessible(client, db, auth_headers):
    """Test that public configuration endpoint is accessible without authentication."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    data = response.json()
    assert "data" in data
    
    config = data["data"]
    assert "app_name" in config
    assert "version" in config
    assert "environment" in config
    assert "google_client_id" in config


async def test_public_config_contains_required_fields(client, db, auth_headers):
    """Test that public configuration contains all required fields."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    config = extract_data(response)

    # Required fields for frontend
    required_fields = [
        "app_name",
        "version", 
        "environment",
        "google_client_id"
    ]
    
    for field in required_fields:
        assert field in config, f"Required field '{field}' missing from public config"
        assert config[field] is not None, f"Required field '{field}' is None"
        assert config[field] != "", f"Required field '{field}' is empty"


async def test_public_config_values_are_valid(client, db, auth_headers):
    """Test that public configuration values are valid."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    config = extract_data(response)

    # Validate app_name
    assert isinstance(config["app_name"], str)
    assert len(config["app_name"]) > 0
    
    # Validate version format (should be semantic version)
    version = config["version"]
    assert isinstance(version, str)
    assert len(version.split(".")) >= 2  # At least major.minor
    
    # Validate environment
    environment = config["environment"]
    assert isinstance(environment, str)
    assert environment in ["development", "staging", "production", "test"]
    
    # Validate Google client ID format
    google_client_id = config["google_client_id"]
    assert isinstance(google_client_id, str)
    assert len(google_client_id) > 0
    assert ".apps.googleusercontent.com" in google_client_id


async def test_config_endpoint_no_sensitive_data(client, db, auth_headers):
    """Test that public config endpoint doesn't expose sensitive data."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    config = extract_data(response)

    # Sensitive fields that should NOT be in public config
    sensitive_fields = [
        "database_url",
        "secret_key",
        "jwt_secret",
        "google_client_secret",
        "api_key",
        "password",
        "token"
    ]
    
    for field in sensitive_fields:
        assert field not in config, f"Sensitive field '{field}' found in public config"
    
    # Check that no values look like secrets
    for key, value in config.items():
        if isinstance(value, str):
            # Check for common secret patterns
            assert not value.startswith("sk-"), f"Field '{key}' looks like an API key"
            assert not value.startswith("Bearer "), f"Field '{key}' looks like a token"
            assert len(value) < 200, f"Field '{key}' is suspiciously long (might be a secret)"


async def test_config_consistency_across_requests(client, db, auth_headers):
    """Test that configuration values are consistent across multiple requests."""
    # Make multiple requests
    responses = []
    for _ in range(3):
        response = await client.get("/api/v1/config/public")
        assert response.status_code == 200
        responses.append(extract_data(response))

    # All responses should be identical
    first_config = responses[0]
    for config in responses[1:]:
        assert config == first_config, "Configuration values should be consistent across requests"


def _is_json_serializable(value) -> bool:
    """Check if a value is JSON serializable (basic types or nested dicts/lists)."""
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_serializable(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_is_json_serializable(item) for item in value)
    return False


async def test_config_response_format(client, db, auth_headers):
    """Test that configuration response follows expected format."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200

    # Check response structure
    data = response.json()
    assert isinstance(data, dict)
    assert "data" in data
    assert isinstance(data["data"], dict)

    # Check that all values are JSON serializable types (including nested objects)
    config = extract_data(response)
    for key, value in config.items():
        assert isinstance(key, str), f"Config key '{key}' is not a string"
        assert _is_json_serializable(value), \
            f"Config value for '{key}' is not a valid JSON type"

    # Verify upload_restrictions structure if present
    if "upload_restrictions" in config:
        ur = config["upload_restrictions"]
        assert isinstance(ur, dict), "upload_restrictions should be a dict"
        assert "allowed_types" in ur, "upload_restrictions should have allowed_types"
        assert "max_size_bytes" in ur, "upload_restrictions should have max_size_bytes"
        assert isinstance(ur["allowed_types"], list), "allowed_types should be a list"
        assert isinstance(ur["max_size_bytes"], int), "max_size_bytes should be an int"

    # Verify kb_upload_restrictions structure if present
    if "kb_upload_restrictions" in config:
        kbur = config["kb_upload_restrictions"]
        assert isinstance(kbur, dict), "kb_upload_restrictions should be a dict"
        assert "allowed_types" in kbur, "kb_upload_restrictions should have allowed_types"
        assert "max_size_bytes" in kbur, "kb_upload_restrictions should have max_size_bytes"


async def test_config_caching_headers(client, db, auth_headers):
    """Test that configuration endpoint has appropriate caching headers."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    # Configuration should be cacheable since it doesn't change often
    # Check that response doesn't have no-cache headers
    headers = response.headers
    
    # Should not have strict no-cache directives for public config
    cache_control = headers.get("cache-control", "").lower()
    assert "no-store" not in cache_control, "Public config should be cacheable"


async def test_config_endpoint_performance(client, db, auth_headers):
    """Test that configuration endpoint responds quickly."""
    import time
    
    start_time = time.time()
    response = await client.get("/api/v1/config/public")
    end_time = time.time()
    
    assert response.status_code == 200
    
    # Configuration endpoint should be very fast (under 100ms)
    response_time = end_time - start_time
    assert response_time < 0.1, f"Config endpoint too slow: {response_time:.3f}s"


async def test_config_with_different_http_methods(client, db, auth_headers):
    """Test that configuration endpoint only accepts GET requests."""
    # GET should work
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    # Other methods should not be allowed
    methods_to_test = ["POST", "PUT", "DELETE", "PATCH"]
    
    for method in methods_to_test:
        if method == "POST":
            response = await client.post("/api/v1/config/public", json={})
        elif method == "PUT":
            response = await client.put("/api/v1/config/public", json={})
        elif method == "DELETE":
            response = await client.delete("/api/v1/config/public")
        elif method == "PATCH":
            response = await client.patch("/api/v1/config/public", json={})
        
        # Should return method not allowed or not found
        assert response.status_code in [405, 404], \
            f"{method} should not be allowed on config endpoint"


async def test_config_version_format(client, db, auth_headers):
    """Test that version follows semantic versioning format."""
    response = await client.get("/api/v1/config/public")
    assert response.status_code == 200
    
    config = extract_data(response)
    version = config["version"]
    
    # Basic semantic version validation
    parts = version.split(".")
    assert len(parts) >= 2, f"Version should have at least major.minor: {version}"
    
    # First two parts should be numeric
    try:
        int(parts[0])  # major
        int(parts[1])  # minor
    except ValueError:
        assert False, f"Version major.minor should be numeric: {version}"


class ConfigurationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Configuration functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all configuration test functions."""
        return [
            test_public_config_endpoint_accessible,
            test_public_config_contains_required_fields,
            test_public_config_values_are_valid,
            test_config_endpoint_no_sensitive_data,
            test_config_consistency_across_requests,
            test_config_response_format,
            test_config_caching_headers,
            test_config_endpoint_performance,
            test_config_with_different_http_methods,
            test_config_version_format,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Configuration Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for configuration system and public config endpoint"
    
    def get_cli_examples(self) -> str:
        """Return configuration-specific CLI examples."""
        return """
Examples:
  python tests/test_config_integration.py                        # Run all config tests
  python tests/test_config_integration.py --list                 # List available tests
  python tests/test_config_integration.py --test test_public_config_endpoint_accessible
  python tests/test_config_integration.py --pattern "public"     # Run public config tests
  python tests/test_config_integration.py --pattern "security"   # Run security-related tests
        """


if __name__ == "__main__":
    suite = ConfigurationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
