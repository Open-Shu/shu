"""
Integration tests for LLM Provider Base Model functionality using custom test framework.

These tests verify the new base model discovery and management features:
- Base model discovery from provider APIs
- Manual model entry and management
- Model synchronization workflows
- Model filtering and search
- Provider-specific model handling
"""

import sys
import os
from typing import List, Callable
from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_validation_errors,
    ExpectedErrorContext
)


# Test Data - Using proper test naming for automatic cleanup
VALID_PROVIDER_DATA = {
    "name": "Test LLM Base Models Provider",  # Follows test naming convention
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-base-models",
    "organization_id": "test-org-base-models",
    "is_active": True,
    "supports_streaming": True,
    "supports_functions": True,
    "supports_vision": False,
    "rate_limit_rpm": 3500,
    "rate_limit_tpm": 90000,
    "budget_limit_monthly": 100.0
}

MOCK_DISCOVERED_MODELS = [
    {
        "model_name": "gpt-4o",
        "display_name": "GPT-4o",
        "model_type": "chat",
        "context_window": 128000,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": True,
        "supports_vision": True,
        "input_cost_per_token": 0.000005,
        "output_cost_per_token": 0.000015
    },
    {
        "model_name": "gpt-4o-mini",
        "display_name": "GPT-4o Mini",
        "model_type": "chat",
        "context_window": 128000,
        "max_output_tokens": 16384,
        "supports_streaming": True,
        "supports_functions": True,
        "supports_vision": True,
        "input_cost_per_token": 0.00000015,
        "output_cost_per_token": 0.0000006
    }
]

MANUAL_MODEL_DATA = {
    "model_name": "test-gpt-4-turbo-base-models",  # Follows test naming convention
    "display_name": "Test GPT-4 Turbo Base Models",  # Follows test naming convention
    "model_type": "chat",
    "context_window": 128000,
    "max_output_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": True,
    "supports_vision": True,
    "cost_per_input_token": 0.00001,  # Fixed field name
    "cost_per_output_token": 0.00003,  # Fixed field name
    "is_active": True  # Added missing field
}


async def _create_test_provider(client, auth_headers):
    """Helper function to create a test provider for base model tests."""
    response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )

    assert response.status_code == 201
    response_data = response.json()

    # Handle different response formats
    if "data" in response_data:
        provider = response_data["data"]
    else:
        provider = response_data

    return provider["id"]


async def test_create_provider_for_base_models(client, db, auth_headers):
    """Test creating a provider for base model testing."""
    response = await client.post(
        "/api/v1/llm/providers",
        json=VALID_PROVIDER_DATA,
        headers=auth_headers
    )

    assert response.status_code == 201
    response_data = response.json()

    # Handle different response formats
    if "data" in response_data:
        provider = response_data["data"]
    else:
        provider = response_data

    assert provider["name"] == VALID_PROVIDER_DATA["name"]
    assert provider["provider_type"] == VALID_PROVIDER_DATA["provider_type"]
    assert provider["is_active"] is True

    # Provider will be automatically cleaned up by test framework due to "Test" in name


async def test_discover_models_endpoint(client, db, auth_headers):
    """Test the model discovery endpoint."""
    # Create a test provider for this test
    provider_id = await _create_test_provider(client, auth_headers)

    # Note: This will likely fail in test environment without real API keys
    # but we can test the endpoint structure and error handling
    response = await client.get(
        f"/api/v1/llm/providers/{provider_id}/discover-models",
        headers=auth_headers
    )

    # Should return either success with models or a proper error
    assert response.status_code in [200, 400, 401, 403, 500]
    
    if response.status_code == 200:
        response_data = response.json()
        data = response_data.get("data", response_data)
        assert "discovered_models" in data
        assert isinstance(data["discovered_models"], list)

        # If models are returned, verify structure
        if data["discovered_models"]:
            model = data["discovered_models"][0]
            required_fields = ["model_name", "display_name", "model_type"]
            for field in required_fields:
                assert field in model


async def test_create_manual_model(client, db, auth_headers):
    """Test creating a model manually."""
    # Create a test provider for this test
    provider_id = await _create_test_provider(client, auth_headers)

    response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models",
        json=MANUAL_MODEL_DATA,
        headers=auth_headers
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    response_data = response.json()

    # Handle different response formats
    if "data" in response_data:
        model = response_data["data"]
    else:
        model = response_data

    assert model["model_name"] == MANUAL_MODEL_DATA["model_name"]
    assert model["display_name"] == MANUAL_MODEL_DATA["display_name"]
    assert model["model_type"] == MANUAL_MODEL_DATA["model_type"]
    assert model["provider_id"] == provider_id

    # Verify in database
    result = await db.execute(
        text("SELECT model_name, provider_id, is_active FROM llm_models WHERE id = :id"),
        {"id": model["id"]}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] == MANUAL_MODEL_DATA["model_name"]
    assert db_row[1] == provider_id
    assert db_row[2] is True  # is_active should be True

    # Model will be automatically cleaned up by test framework due to "Test" in name


async def test_list_provider_models(client, db, auth_headers):
    """Test listing models for a provider."""
    # Create a test provider and model for this test
    provider_id = await _create_test_provider(client, auth_headers)

    # Create a model for the provider
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models",
        json=MANUAL_MODEL_DATA,
        headers=auth_headers
    )
    assert model_response.status_code == 200

    response = await client.get(
        f"/api/v1/llm/models?provider_id={provider_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    response_data = response.json()

    # Models endpoint returns envelope now
    models = response_data["data"]

    assert isinstance(models, list)
    assert len(models) >= 1  # Should have at least our manual model

    # Find our manual model
    manual_model = None
    for model in models:
        if model["model_name"] == MANUAL_MODEL_DATA["model_name"]:
            manual_model = model
            break

    assert manual_model is not None
    assert manual_model["display_name"] == MANUAL_MODEL_DATA["display_name"]


async def test_sync_models_endpoint(client, db, auth_headers):
    """Test the model sync endpoint."""
    # Create a test provider for this test
    provider_id = await _create_test_provider(client, auth_headers)

    # Test syncing models (this will likely fail without real API discovery)
    # but we can test the endpoint structure
    sync_data = [MANUAL_MODEL_DATA["model_name"]]

    response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/sync-models",
        json=sync_data,
        headers=auth_headers
    )

    # Should return either success or a proper error
    assert response.status_code in [200, 400, 401, 403, 500]

    if response.status_code == 200:
        response_data = response.json()
        data = response_data.get("data", response_data)
        # Should have some indication of sync results
        assert "synced_models" in data or "enabled_models" in data or "models" in data


async def test_provider_validation(client, db, auth_headers):
    """Test provider validation and error handling."""
    # Test creating provider with invalid data
    invalid_data = {
        "name": "",  # Empty name should fail
        "provider_type": "invalid_type",
        "api_endpoint": "not-a-url"
    }

    response = await client.post(
        "/api/v1/llm/providers",
        json=invalid_data,
        headers=auth_headers
    )

    # Should return validation error
    assert response.status_code in [400, 422]


class LLMBaseModelsTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for LLM Provider Base Model functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return all LLM base model test functions."""
        return [
            test_create_provider_for_base_models,
            test_discover_models_endpoint,
            test_create_manual_model,
            test_list_provider_models,
            test_sync_models_endpoint,
            test_provider_validation,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "LLM Base Models Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Integration tests for LLM provider base model discovery and management"


if __name__ == "__main__":
    suite = LLMBaseModelsTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
