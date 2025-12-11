"""
Template for creating new integration test suites.

Copy this file and customize it for your feature area.
Replace all instances of "Feature" with your actual feature name.

Example usage:
1. Copy this file to test_your_feature_integration.py
2. Replace "Feature" with your feature name (e.g., "Knowledge Base", "Authentication")
3. Implement your test functions
4. Update the test list in get_test_functions()
5. Run with: python tests/test_your_feature_integration.py
"""

import sys
import os
from typing import List, Callable
from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data


# Test Data - Define your test data constants here
SAMPLE_TEST_DATA = {
    "name": "Test Item",
    "description": "Test description",
    # Add your test data here
}


# Test Functions - Implement your actual test functions here
async def test_feature_health_check(client, db, auth_headers):
    """Test that the feature endpoints are accessible."""
    # Example: Test a basic endpoint
    response = await client.get("/api/v1/your-feature/health", headers=auth_headers)
    assert response.status_code == 200


async def test_create_feature_item_success(client, db, auth_headers):
    """Test successful creation of a feature item."""
    # Example: Create an item via API
    response = await client.post(
        "/api/v1/your-feature/items",
        json=SAMPLE_TEST_DATA,
        headers=auth_headers
    )
    
    assert response.status_code == 201
    data = extract_data(response)

    # Verify response structure
    assert data["name"] == SAMPLE_TEST_DATA["name"]
    assert "id" in data

    # Verify data was stored in database
    result = await db.execute(
        text("SELECT name FROM your_feature_table WHERE name = :name"),
        {"name": SAMPLE_TEST_DATA["name"]}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] == SAMPLE_TEST_DATA["name"]
    
    return data["id"]  # Return for use in other tests


async def test_get_feature_item_by_id(client, db, auth_headers):
    """Test retrieving a specific item by ID."""
    # First create an item
    create_response = await client.post(
        "/api/v1/your-feature/items",
        json=SAMPLE_TEST_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    item_id = extract_data(create_response)["id"]

    # Now retrieve it
    response = await client.get(f"/api/v1/your-feature/items/{item_id}", headers=auth_headers)
    assert response.status_code == 200
    
    item = extract_data(response)
    assert item["id"] == item_id
    assert item["name"] == SAMPLE_TEST_DATA["name"]


async def test_update_feature_item(client, db, auth_headers):
    """Test updating an item."""
    # Create item first
    create_response = await client.post(
        "/api/v1/your-feature/items",
        json=SAMPLE_TEST_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    item_id = extract_data(create_response)["id"]

    # Update the item
    update_data = {
        "name": "Updated Test Item",
        "description": "Updated description"
    }
    
    response = await client.put(
        f"/api/v1/your-feature/items/{item_id}",
        json=update_data,
        headers=auth_headers
    )
    assert response.status_code == 200
    
    updated_item = extract_data(response)
    assert updated_item["name"] == "Updated Test Item"
    assert updated_item["description"] == "Updated description"

    # Verify in database
    result = await db.execute(
        text("SELECT name, description FROM your_feature_table WHERE id = :id"),
        {"id": item_id}
    )
    db_row = result.fetchone()
    assert db_row[0] == "Updated Test Item"
    assert db_row[1] == "Updated description"


async def test_delete_feature_item(client, db, auth_headers):
    """Test deleting an item."""
    # Create item first
    create_response = await client.post(
        "/api/v1/your-feature/items",
        json=SAMPLE_TEST_DATA,
        headers=auth_headers
    )
    assert create_response.status_code == 201
    item_id = extract_data(create_response)["id"]

    # Delete the item
    response = await client.delete(f"/api/v1/your-feature/items/{item_id}", headers=auth_headers)
    assert response.status_code == 200  # or 204, depending on your API
    
    # Verify it's gone from database
    result = await db.execute(
        text("SELECT COUNT(*) FROM your_feature_table WHERE id = :id"),
        {"id": item_id}
    )
    count = result.scalar()
    assert count == 0
    
    # Verify 404 when trying to get deleted item
    get_response = await client.get(f"/api/v1/your-feature/items/{item_id}", headers=auth_headers)
    assert get_response.status_code == 404


async def test_unauthorized_access(client, db, auth_headers):
    """Test that endpoints require authentication."""
    # Try to access without auth headers
    response = await client.get("/api/v1/your-feature/items")
    assert response.status_code == 401
    
    # Try to create without auth
    response = await client.post("/api/v1/your-feature/items", json=SAMPLE_TEST_DATA)
    assert response.status_code == 401


async def test_invalid_data_validation(client, db, auth_headers):
    """Test that invalid data is rejected."""
    # Test missing required fields
    invalid_data = {
        "description": "Missing name field"
        # Missing required 'name' field
    }
    
    response = await client.post(
        "/api/v1/your-feature/items",
        json=invalid_data,
        headers=auth_headers
    )
    assert response.status_code == 422  # Validation error


# Test Suite Class
class FeatureTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Feature functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all feature test functions."""
        return [
            test_feature_health_check,
            test_create_feature_item_success,
            test_get_feature_item_by_id,
            test_update_feature_item,
            test_delete_feature_item,
            test_unauthorized_access,
            test_invalid_data_validation,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Feature Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Feature CRUD operations and API functionality"
    
    def get_cli_examples(self) -> str:
        """Return feature-specific CLI examples."""
        return """
Examples:
  python tests/test_feature_integration.py                       # Run all feature tests
  python tests/test_feature_integration.py --list               # List available tests
  python tests/test_feature_integration.py --test test_create_feature_item_success
  python tests/test_feature_integration.py --pattern create     # Run all 'create' tests
  python tests/test_feature_integration.py --pattern "auth"     # Run auth-related tests
        """


if __name__ == "__main__":
    suite = FeatureTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
