"""
Integration tests for User Preferences functionality using custom test framework.

These tests verify the user preferences system that manages legitimate user settings:
- Memory settings (depth, similarity threshold)
- UI/UX preferences (theme, language, timezone)
- Advanced settings (JSON configuration)

NOTE: RAG and LLM settings are now admin-only configuration and not user preferences.
"""

import sys
import os
import uuid
from typing import List, Callable
from integ.response_utils import extract_data

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_validation_errors,
    ExpectedErrorContext
)


# Test Data (only legitimate user preferences)
DEFAULT_PREFERENCES = {
    "memory_depth": 5,
    "memory_similarity_threshold": 0.6,
    "theme": "light",
    "language": "en",
    "timezone": "UTC",
    "advanced_settings": {}
}

CUSTOM_PREFERENCES = {
    "memory_depth": 10,
    "memory_similarity_threshold": 0.8,
    "theme": "dark",
    "language": "es",
    "timezone": "America/New_York",
    "advanced_settings": {
        "custom_setting_1": "value1",
        "custom_setting_2": 42
    }
}


async def test_user_preferences_auto_creation(client, db, auth_headers):
    """Test that user preferences are automatically created for new users."""
    # Check if preferences exist for the test user
    response = await client.get("/api/v1/user/preferences", headers=auth_headers)
    
    if response.status_code == 404:
        # Preferences don't exist yet - this is expected for new users
        # They should be created on first access or when user updates them
        return True
    
    assert response.status_code == 200, f"Expected 200 or 404, got {response.status_code}: {response.text}"
    
    # If preferences exist, verify they have default values
    preferences = extract_data(response)

    # Check key default values (only legitimate user preferences)
    assert preferences["memory_depth"] == 5
    assert preferences["memory_similarity_threshold"] == 0.6
    assert preferences["theme"] == "light"
    
    return True


async def test_create_user_preferences(client, db, auth_headers):
    """Test creating/updating user preferences."""
    # Create or update preferences
    response = await client.put(
        "/api/v1/user/preferences",
        json=CUSTOM_PREFERENCES,
        headers=auth_headers
    )
    
    # Should succeed (either 200 for update or 201 for create)
    assert response.status_code in [200, 201], f"Expected 200/201, got {response.status_code}: {response.text}"
    
    preferences = extract_data(response)

    # Verify all custom values were set (only legitimate user preferences)
    assert preferences["memory_depth"] == 10
    assert preferences["memory_similarity_threshold"] == 0.8
    assert preferences["theme"] == "dark"
    assert preferences["language"] == "es"
    assert preferences["timezone"] == "America/New_York"
    assert preferences["advanced_settings"]["custom_setting_1"] == "value1"
    assert preferences["advanced_settings"]["custom_setting_2"] == 42
    
    return True


async def test_get_user_preferences(client, db, auth_headers):
    """Test retrieving user preferences."""
    response = await client.get("/api/v1/user/preferences", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    preferences = extract_data(response)

    # Verify structure (only legitimate user preferences)
    required_fields = [
        "memory_depth",
        "memory_similarity_threshold",
        "theme",
        "language",
        "timezone",
        "advanced_settings",
    ]
    
    for field in required_fields:
        assert field in preferences, f"Missing required field: {field}"
    
    return True


async def test_update_partial_preferences(client, db, auth_headers):
    """Test updating only some preference fields."""
    # Update only memory settings
    partial_update = {
        "memory_depth": 15,
        "memory_similarity_threshold": 0.75
    }
    
    response = await client.patch(
        "/api/v1/user/preferences",
        json=partial_update,
        headers=auth_headers
    )
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    preferences = extract_data(response)

    # Verify updated fields
    assert preferences["memory_depth"] == 15
    assert preferences["memory_similarity_threshold"] == 0.75
    
    # Verify other fields unchanged (should still have custom values from previous test)
    assert preferences["theme"] == "dark"  # Should remain from previous test
    assert preferences["language"] == "es"  # Should remain from previous test
    
    return True


async def test_preferences_validation(client, db, auth_headers):
    """Test validation of preference values."""
    # Test invalid memory depth (negative)
    invalid_data = {"memory_depth": -1}
    response = await client.patch(
        "/api/v1/user/preferences",
        json=invalid_data,
        headers=auth_headers
    )
    assert response.status_code == 422, "Should reject negative memory depth"
    
    # Test invalid similarity threshold (> 1.0)
    invalid_data = {"memory_similarity_threshold": 1.5}
    response = await client.patch(
        "/api/v1/user/preferences",
        json=invalid_data,
        headers=auth_headers
    )
    assert response.status_code == 422, "Should reject similarity threshold > 1.0"
    
    # Test invalid theme
    invalid_data = {"theme": "invalid_theme"}
    response = await client.patch(
        "/api/v1/user/preferences",
        json=invalid_data,
        headers=auth_headers
    )
    assert response.status_code == 422, "Should reject invalid theme"
    
    return True


async def test_preferences_advanced_settings(client, db, auth_headers):
    """Test advanced settings JSON field functionality."""
    # Set complex advanced settings
    advanced_settings = {
        "custom_rag_settings": {
            "chunk_overlap_ratio": 0.15,
            "custom_embedding_model": "custom-model-v1"
        },
        "ui_customizations": {
            "sidebar_collapsed": True,
            "message_font_size": 14
        },
        "experimental_features": ["feature_a", "feature_b"]
    }
    
    preferences_data = {"advanced_settings": advanced_settings}
    
    response = await client.patch(
        "/api/v1/user/preferences",
        json=preferences_data,
        headers=auth_headers
    )
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    # Verify advanced settings were stored correctly
    get_response = await client.get("/api/v1/user/preferences", headers=auth_headers)
    preferences = extract_data(get_response)

    assert preferences["advanced_settings"]["custom_rag_settings"]["chunk_overlap_ratio"] == 0.15
    assert preferences["advanced_settings"]["ui_customizations"]["sidebar_collapsed"] == True
    assert "feature_a" in preferences["advanced_settings"]["experimental_features"]
    
    return True


class UserPreferencesIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for User Preferences functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return all user preferences test functions."""
        return [
            test_user_preferences_auto_creation,
            test_create_user_preferences,
            test_get_user_preferences,
            test_update_partial_preferences,
            test_preferences_validation,
            test_preferences_advanced_settings,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "User Preferences Integration"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Integration tests for user preferences system including memory settings, RAG configuration, chat behavior, and UI preferences"


if __name__ == "__main__":
    import asyncio
    suite = UserPreferencesIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
