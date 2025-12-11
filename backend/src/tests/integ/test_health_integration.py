"""
Health Monitoring Integration Tests for Shu

These tests cover comprehensive health checks, monitoring endpoints,
and system status verification.
"""

import sys
import os
from typing import List, Callable
import time

from integ.base_integration_test import BaseIntegrationTestSuite
from sqlalchemy import text


async def test_basic_health_check(client, db, auth_headers):
    """Test basic health check endpoint."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    
    # Check required health fields
    assert "status" in data
    assert data["status"] in ["healthy", "warning", "unhealthy"]
    assert "timestamp" in data
    assert "version" in data
    assert "environment" in data
    assert "checks" in data


async def test_health_check_performance(client, db, auth_headers):
    """Test that health check responds quickly."""
    start_time = time.time()
    response = await client.get("/api/v1/health", headers=auth_headers)
    end_time = time.time()

    assert response.status_code == 200
    # Health check should be fast (under 2 seconds)
    assert (end_time - start_time) < 2.0, "Health check should respond quickly"


async def test_health_check_database_connectivity(client, db, auth_headers):
    """Test that health check verifies database connectivity."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()["data"]
    checks = data["checks"]
    
    # Should have database connectivity check
    assert "database" in checks or "db" in checks or any("database" in str(key).lower() for key in checks.keys())


async def test_readiness_probe(client, db, auth_headers):
    """Test Kubernetes readiness probe endpoint."""
    response = await client.get("/api/v1/health/readiness")
    assert response.status_code in [200, 503]  # 503 if not ready
    
    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    
    # Check readiness fields
    assert "ready" in data
    assert isinstance(data["ready"], bool)
    assert "timestamp" in data
    assert "checks" in data
    
    if response.status_code == 503:
        # If not ready, should have errors
        assert "errors" in data
        assert isinstance(data["errors"], list)


async def test_liveness_probe(client, db, auth_headers):
    """Test Kubernetes liveness probe endpoint."""
    response = await client.get("/api/v1/health/liveness")
    assert response.status_code == 200
    
    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    
    # Check liveness fields
    assert "alive" in data
    assert data["alive"] is True
    assert "timestamp" in data
    assert "pid" in data
    assert "version" in data


async def test_liveness_probe_performance(client, db, auth_headers):
    """Test that liveness probe is very fast."""
    start_time = time.time()
    response = await client.get("/api/v1/health/liveness")
    end_time = time.time()
    
    assert response.status_code == 200
    # Liveness should be extremely fast (under 0.5 seconds)
    assert (end_time - start_time) < 0.5, "Liveness probe should be very fast"


async def test_database_health_check(client, db, auth_headers):
    """Test detailed database health check endpoint."""
    response = await client.get("/api/v1/health/database", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]

    # Check database health fields
    assert "status" in data
    assert data["status"] in ["healthy", "warning", "unhealthy"]

    # Database health should have checks field with connectivity info
    if "checks" in data:
        checks = data["checks"]
        assert isinstance(checks, dict)
        # Should have some database-related checks
        expected_checks = ["connectivity", "connection", "version", "connection_pool"]
        assert any(check in checks for check in expected_checks), f"Expected database checks in: {checks.keys()}"


async def test_health_endpoints_no_auth_required(client, db, auth_headers):
    """Test that readiness/liveness endpoints don't require authentication."""
    # Test without auth headers - readiness and liveness should be public
    endpoints = [
        "/api/v1/health/readiness",
        "/api/v1/health/liveness",
    ]

    for endpoint in endpoints:
        response = await client.get(endpoint)
        # Should not require authentication
        assert response.status_code in [200, 503], f"Health endpoint {endpoint} should not require auth"


async def test_health_check_consistency(client, db, auth_headers):
    """Test that health checks are consistent across multiple calls."""
    responses = []
    
    # Make multiple health check calls
    for _ in range(3):
        response = await client.get("/api/v1/health", headers=auth_headers)
        assert response.status_code == 200
        responses.append(response.json())
        time.sleep(0.1)  # Small delay between calls
    
    # Status should be consistent (allowing for timestamp differences)
    first_status = responses[0]["data"]["status"]
    for response in responses[1:]:
        assert response["data"]["status"] == first_status, "Health status should be consistent"


async def test_health_check_system_info(client, db, auth_headers):
    """Test that health check includes system information."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()["data"]
    
    # Should include system information
    system_fields = ["version", "environment", "timestamp"]
    for field in system_fields:
        assert field in data, f"Health check should include {field}"
    
    # Version should be a string
    assert isinstance(data["version"], str)
    assert len(data["version"]) > 0
    
    # Environment should be valid
    assert data["environment"] in ["development", "staging", "production", "test"]


async def test_readiness_probe_database_check(client, db, auth_headers):
    """Test that readiness probe checks database connectivity."""
    response = await client.get("/api/v1/health/readiness")
    assert response.status_code in [200, 503]
    
    data = response.json()["data"]
    checks = data["checks"]
    
    # Should check database connectivity for readiness
    assert "database" in checks or "db" in checks or any("database" in str(key).lower() for key in checks.keys())


async def test_health_endpoints_response_format(client, db, auth_headers):
    """Test that all health endpoints follow consistent response format."""
    public_endpoints = [
        "/api/v1/health/readiness",
        "/api/v1/health/liveness",
    ]

    auth_endpoints = [
        "/api/v1/health",
        "/api/v1/health/database",
    ]

    # Test public endpoints
    for endpoint in public_endpoints:
        response = await client.get(endpoint)
        assert response.status_code in [200, 503]

        # Should follow Shu response envelope format
        response_data = response.json()
        assert "data" in response_data, f"Endpoint {endpoint} should use envelope format"

        # Should have timestamp
        data = response_data["data"]
        assert "timestamp" in data, f"Endpoint {endpoint} should include timestamp"

    # Test authenticated endpoints
    for endpoint in auth_endpoints:
        response = await client.get(endpoint, headers=auth_headers)
        assert response.status_code in [200, 503]
        
        # Should follow Shu response envelope format
        response_data = response.json()
        assert "data" in response_data, f"Endpoint {endpoint} should use envelope format"
        
        # Should have timestamp
        data = response_data["data"]
        assert "timestamp" in data, f"Endpoint {endpoint} should include timestamp"


async def test_health_check_error_handling(client, db, auth_headers):
    """Test health check behavior under error conditions."""
    # Test with invalid path (with auth since health subroutes may require auth)
    response = await client.get("/api/v1/health/invalid", headers=auth_headers)
    assert response.status_code == 404

    # Test with invalid method on secured endpoint (using auth)
    response = await client.post("/api/v1/health", headers=auth_headers)
    assert response.status_code == 405  # Method not allowed


async def test_health_metrics_collection(client, db, auth_headers):
    """Test that health endpoints collect useful metrics."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()["data"]
    
    # Should have execution time or performance metrics
    performance_indicators = [
        "execution_time", "response_time", "duration", 
        "checks", "memory", "cpu"
    ]
    
    # At least some performance indicators should be present
    has_performance_data = any(
        indicator in data or 
        any(indicator in str(key).lower() for key in data.keys())
        for indicator in performance_indicators
    )
    
    assert has_performance_data, "Health check should include performance metrics"


class HealthIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for health monitoring functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all health integration test functions."""
        return [
            test_basic_health_check,
            test_health_check_performance,
            test_health_check_database_connectivity,
            test_readiness_probe,
            test_liveness_probe,
            test_liveness_probe_performance,
            test_database_health_check,
            test_health_endpoints_no_auth_required,
            test_health_check_consistency,
            test_health_check_system_info,
            test_readiness_probe_database_check,
            test_health_endpoints_response_format,
            test_health_check_error_handling,
            test_health_metrics_collection,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Health Monitoring Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for health monitoring, readiness, and liveness probes"
    
    def get_cli_examples(self) -> str:
        """Return health-specific CLI examples."""
        return """
Examples:
  python tests/test_health_integration.py                          # Run all health tests
  python tests/test_health_integration.py --list                   # List available tests
  python tests/test_health_integration.py --test test_basic_health_check
  python tests/test_health_integration.py --pattern "readiness"    # Run readiness tests
  python tests/test_health_integration.py --pattern "liveness"     # Run liveness tests
        """


if __name__ == "__main__":
    suite = HealthIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
