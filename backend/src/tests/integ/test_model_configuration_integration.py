"""
Integration tests for Model Configuration functionality using custom test framework.

These tests verify the model configuration system that combines:
- LLM providers and models
- Prompts (optional)
- Knowledge bases (optional)
- User access and permissions
- CRUD operations and validation
"""

import sys
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import create_active_user_headers
from integ.response_utils import extract_data

SIDE_CALL_SETTING_KEY = "side_call_model_config_id"


async def _clear_side_call_setting(db) -> None:
    """Ensure side-call configuration starts from a clean state."""
    await db.execute(
        text("DELETE FROM system_settings WHERE key = :key"),
        {"key": SIDE_CALL_SETTING_KEY},
    )
    await db.commit()


# Test Data
PROVIDER_DATA = {
    "name": "Test Model Configuration Provider",  # Follows test naming convention
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-configs",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "test-gpt-4o-model-config",  # Follows test naming convention
    "display_name": "Test GPT-4o Model Config",  # Follows test naming convention
    "model_type": "chat",
    "context_window": 128000,
    "max_output_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": True,
    "cost_per_input_token": 0.000005,
    "cost_per_output_token": 0.000015,
    "is_active": True,
}

PROMPT_DATA = {
    "name": "Test Model Configuration Prompt",  # Follows test naming convention
    "content": "You are a helpful AI assistant for testing model configurations.",
    "entity_type": "llm_model",
    "description": "Test prompt for model configuration integration tests",
}

KB_DATA = {
    "name": "Test Model Configuration KB",  # Follows test naming convention
    "description": "Test KB for model configuration tests",
    "source_type": "google_drive",
}

CONFIG_DATA = {
    "name": "Test Model Configuration",  # Already follows test naming convention
    "description": "Integration test configuration combining model, prompt, and KB",
    "is_active": True,
}


async def _create_test_dependencies(client, auth_headers):
    """Helper function to create test dependencies for model configuration tests."""
    # Create provider
    response = await client.post("/api/v1/llm/providers", json=PROVIDER_DATA, headers=auth_headers)
    assert response.status_code == 201
    provider_id = extract_data(response)["id"]

    # Create model
    response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=MODEL_DATA, headers=auth_headers)
    assert response.status_code == 200
    model_id = extract_data(response)["id"]

    # Create prompt
    response = await client.post("/api/v1/prompts/", json=PROMPT_DATA, headers=auth_headers)
    assert response.status_code == 201
    prompt_id = extract_data(response)["id"]

    # Create knowledge base
    response = await client.post("/api/v1/knowledge-bases", json=KB_DATA, headers=auth_headers)
    assert response.status_code == 201
    kb_id = extract_data(response)["id"]

    return provider_id, model_id, prompt_id, kb_id


async def test_setup_test_dependencies(client, db, auth_headers):
    """Test creating dependencies for model configuration tests."""
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Verify all dependencies were created
    assert provider_id is not None
    assert model_id is not None
    assert prompt_id is not None
    assert kb_id is not None


async def test_create_model_configuration_minimal(client, db, auth_headers):
    """Test creating a minimal model configuration (model only)."""
    # Create test dependencies
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    config_data = {
        **CONFIG_DATA,
        "name": "Test Minimal Model Configuration",  # Follows test naming convention
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    config = extract_data(response)

    assert config["name"] == config_data["name"]
    assert config["llm_provider_id"] == provider_id
    assert config["model_name"] == MODEL_DATA["model_name"]
    assert config["prompt_id"] is None
    assert config["knowledge_bases"] == []
    assert config["has_knowledge_bases"] is False
    assert config["knowledge_base_count"] == 0

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_create_model_configuration_with_prompt(client, db, auth_headers):
    """Test creating a model configuration with a prompt."""
    # Create test dependencies
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    config_data = {
        **CONFIG_DATA,
        "name": "Test Model Configuration with Prompt",  # Follows test naming convention
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "prompt_id": prompt_id,
        "created_by": "test-user",
    }

    response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert response.status_code == 201

    config = extract_data(response)

    assert config["prompt_id"] == prompt_id
    assert config["prompt"]["name"] == PROMPT_DATA["name"]
    assert config["prompt"]["content"] == PROMPT_DATA["content"]

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_create_model_configuration_full(client, db, auth_headers):
    """Test creating a full model configuration with prompt and knowledge bases."""
    # Create test dependencies
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    config_data = {
        **CONFIG_DATA,
        "name": "Test Full Model Configuration",  # Follows test naming convention
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "prompt_id": prompt_id,
        "knowledge_base_ids": [kb_id],  # Knowledge base attachment works correctly
        "created_by": "test-user",
    }

    response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    config = extract_data(response)

    assert config["prompt_id"] == prompt_id, f"Expected prompt_id {prompt_id}, got {config.get('prompt_id')}"

    # Test that knowledge base attachment works correctly
    assert (
        config["has_knowledge_bases"] is True
    ), f"Expected has_knowledge_bases True, got {config.get('has_knowledge_bases')}"
    assert (
        config["knowledge_base_count"] == 1
    ), f"Expected knowledge_base_count 1, got {config.get('knowledge_base_count')}"
    assert (
        len(config["knowledge_bases"]) == 1
    ), f"Expected 1 knowledge base, got {len(config.get('knowledge_bases', []))}"
    assert (
        config["knowledge_bases"][0]["id"] == kb_id
    ), f"Expected KB id {kb_id}, got {config['knowledge_bases'][0].get('id')}"

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_list_model_configurations(client, db, auth_headers):
    """Test listing model configurations."""
    # Create test dependencies and a configuration to list
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create a test configuration
    config_data = {
        **CONFIG_DATA,
        "name": "Test List Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    create_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert create_response.status_code == 201

    # Now test listing
    response = await client.get("/api/v1/model-configurations", headers=auth_headers)
    assert response.status_code == 200

    data = extract_data(response)
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1  # Should have at least our test configuration

    # Verify our configuration is in the list
    config_names = [config["name"] for config in data["items"]]
    assert "Test List Model Configuration" in config_names

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_regular_user_sees_only_active_configurations(client, db, auth_headers):
    """Regular users should be able to list active configs but never inactive ones."""
    provider_id, _, _, _ = await _create_test_dependencies(client, auth_headers)

    active_name = "Test Active Config Regular User Visibility"
    inactive_name = "Test Inactive Config Regular User Visibility"

    active_config = {
        **CONFIG_DATA,
        "name": active_name,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "is_active": True,
        "created_by": "test-user",
    }
    inactive_config = {
        **CONFIG_DATA,
        "name": inactive_name,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "is_active": False,
        "created_by": "test-user",
    }

    active_resp = await client.post("/api/v1/model-configurations", json=active_config, headers=auth_headers)
    assert active_resp.status_code == 201, active_resp.text
    inactive_resp = await client.post("/api/v1/model-configurations", json=inactive_config, headers=auth_headers)
    assert inactive_resp.status_code == 201, inactive_resp.text

    user_headers = await create_active_user_headers(client, auth_headers, role="regular_user")

    # Default listing should succeed for a regular user and exclude inactive configs
    list_resp = await client.get("/api/v1/model-configurations", headers=user_headers)
    assert list_resp.status_code == 200, list_resp.text
    list_data = extract_data(list_resp)
    list_items = list_data.get("items", list_data)
    list_names = [cfg["name"] for cfg in list_items]
    assert active_name in list_names
    assert inactive_name not in list_names

    # Even when explicitly requesting inactive configs, regular users should not see them
    inactive_list_resp = await client.get("/api/v1/model-configurations?is_active=false", headers=user_headers)
    assert inactive_list_resp.status_code == 200, inactive_list_resp.text
    inactive_list_data = extract_data(inactive_list_resp)
    inactive_items = inactive_list_data.get("items", inactive_list_data)
    inactive_names = [cfg["name"] for cfg in inactive_items]
    assert inactive_name not in inactive_names

    # Configurations will be automatically cleaned up by test framework due to "Test" in name


async def test_regular_user_listing_with_kb_does_not_expose_relationships(client, db, auth_headers):
    """Regular users can list configs that have KBs without seeing KB details or causing errors."""
    # Create test dependencies and a configuration that attaches a KB
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    config_name = "Test Regular User KB Visibility"
    config_payload = {
        **CONFIG_DATA,
        "name": config_name,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "knowledge_base_ids": [kb_id],
        "created_by": "test-user",
    }

    create_resp = await client.post("/api/v1/model-configurations", json=config_payload, headers=auth_headers)
    assert create_resp.status_code == 201, create_resp.text

    # Act as a regular user listing configurations (this previously triggered MissingGreenlet)
    user_headers = await create_active_user_headers(client, auth_headers, role="regular_user")
    list_resp = await client.get("/api/v1/model-configurations", headers=user_headers)
    assert list_resp.status_code == 200, list_resp.text
    list_data = extract_data(list_resp)
    items = list_data.get("items", list_data)

    # For any configurations visible to a regular user, KB relationship
    # details must be hidden. The specific KB-backed configuration created
    # above may or may not be visible depending on RBAC, but the presence of
    # that configuration in the database must not cause errors (MissingGreenlet)
    # and no KB details should leak through this list endpoint.
    for config in items:
        assert config.get("has_knowledge_bases") is False
        assert config.get("knowledge_base_count") == 0
        assert config.get("knowledge_bases") == []

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_get_model_configuration_by_id(client, db, auth_headers):
    """Test getting a specific model configuration by ID."""
    # Create test dependencies and a configuration to retrieve
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create a test configuration (without KBs since KB attachment isn't working)
    config_data = {
        **CONFIG_DATA,
        "name": "Test Get Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "prompt_id": prompt_id,
        "knowledge_base_ids": [kb_id],  # Knowledge base attachment works correctly
        "created_by": "test-user",
    }

    create_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert create_response.status_code == 201
    config_id = extract_data(create_response)["id"]

    # Now test getting by ID
    response = await client.get(f"/api/v1/model-configurations/{config_id}", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    config = extract_data(response)
    assert config["id"] == config_id
    assert config["name"] == "Test Get Model Configuration"

    # Verify relationships are loaded
    assert config["llm_provider"]["id"] == provider_id
    assert config["llm_provider"]["name"] == PROVIDER_DATA["name"]
    assert config["prompt"]["id"] == prompt_id
    # Knowledge base attachment works correctly
    assert len(config["knowledge_bases"]) == 1
    assert config["knowledge_bases"][0]["id"] == kb_id

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_update_model_configuration(client, db, auth_headers):
    """Test updating a model configuration."""
    # Create test dependencies and a configuration to update
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create a test configuration
    config_data = {
        **CONFIG_DATA,
        "name": "Test Update Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    create_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert create_response.status_code == 201
    config_id = extract_data(create_response)["id"]

    # Now test updating
    update_data = {
        "name": "Test Updated Model Configuration",
        "description": "Updated description for testing",
        "is_active": False,
    }

    response = await client.put(
        f"/api/v1/model-configurations/{config_id}",
        json=update_data,
        headers=auth_headers,
    )
    assert response.status_code == 200

    config = extract_data(response)
    assert config["name"] == update_data["name"]
    assert config["description"] == update_data["description"]
    assert config["is_active"] == update_data["is_active"]

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_toggle_off_side_call_clears_config(client, db, auth_headers):
    """Toggling is_side_call_model from True to False clears side-call config."""
    await _clear_side_call_setting(db)

    # Create test dependencies and a side-call-capable configuration
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    create_payload = {
        **CONFIG_DATA,
        "name": "Test Side Call Toggle Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "functionalities": {"side_call": True},
        "is_active": True,
        "is_side_call_model": True,
        "created_by": "test-user",
    }

    create_response = await client.post(
        "/api/v1/model-configurations",
        json=create_payload,
        headers=auth_headers,
    )
    assert create_response.status_code == 201, create_response.text
    config = extract_data(create_response)
    config_id = config["id"]

    # Verify side-call configuration now points at this model configuration
    config_response = await client.get("/api/v1/side-calls/config", headers=auth_headers)
    assert config_response.status_code == 200
    side_call_data = extract_data(config_response)
    assert side_call_data["configured"] is True
    assert side_call_data["side_call_model_config"]["id"] == config_id

    # Toggle off side-caller via model configuration update
    update_payload = {
        "is_side_call_model": False,
    }
    update_response = await client.put(
        f"/api/v1/model-configurations/{config_id}",
        json=update_payload,
        headers=auth_headers,
    )
    assert update_response.status_code == 200, update_response.text

    # Side-call config endpoint should now report unconfigured
    final_response = await client.get("/api/v1/side-calls/config", headers=auth_headers)
    assert final_response.status_code == 200
    final_data = extract_data(final_response)
    assert final_data["configured"] is False
    assert final_data["side_call_model_config"] is None

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_filter_configurations_by_active_status(client, db, auth_headers):
    """Test filtering configurations by active status."""
    # Create test dependencies and configurations with different active status
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create an active configuration
    active_config_data = {
        **CONFIG_DATA,
        "name": "Test Active Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "is_active": True,
        "created_by": "test-user",
    }

    active_response = await client.post("/api/v1/model-configurations", json=active_config_data, headers=auth_headers)
    assert active_response.status_code == 201

    # Create an inactive configuration
    inactive_config_data = {
        **CONFIG_DATA,
        "name": "Test Inactive Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "is_active": False,
        "created_by": "test-user",
    }

    inactive_response = await client.post(
        "/api/v1/model-configurations", json=inactive_config_data, headers=auth_headers
    )
    assert inactive_response.status_code == 201

    # Test active configurations filter
    response = await client.get("/api/v1/model-configurations?is_active=true", headers=auth_headers)
    print(f"Active filter response: {response.status_code}")
    print(f"Active filter text: {response.text}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    active_configs = extract_data(response)["items"]
    active_names = [config["name"] for config in active_configs]
    assert "Test Active Model Configuration" in active_names

    # Test inactive configurations filter
    response = await client.get("/api/v1/model-configurations?is_active=false", headers=auth_headers)
    print(f"Inactive filter response: {response.status_code}")
    print(f"Inactive filter text: {response.text}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    inactive_configs = extract_data(response)["items"]
    inactive_names = [config["name"] for config in inactive_configs]
    assert "Test Inactive Model Configuration" in inactive_names

    # Configurations will be automatically cleaned up by test framework due to "Test" in name


async def test_search_configurations_by_name(client, db, auth_headers):
    """Test searching configurations by name."""
    # Create test dependencies and a configuration to search for
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create a test configuration with searchable name
    config_data = {
        **CONFIG_DATA,
        "name": "Test Searchable Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    create_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert create_response.status_code == 201

    # Test searching by partial name
    response = await client.get("/api/v1/model-configurations?search=searchable", headers=auth_headers)
    assert response.status_code == 200

    configs = extract_data(response)["items"]
    assert len(configs) >= 1

    # Should find our searchable config
    found_searchable = any("searchable" in config["name"].lower() for config in configs)
    assert found_searchable

    # Configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_create_configuration_duplicate_name(client, db, auth_headers):
    """Test that duplicate configuration names are rejected."""
    # Create test dependencies
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create first configuration
    config_data = {
        **CONFIG_DATA,
        "name": "Test Duplicate Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    first_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert first_response.status_code == 201

    # Try to create second configuration with same name
    duplicate_data = {
        **CONFIG_DATA,
        "name": "Test Duplicate Model Configuration",  # Same name
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    response = await client.post("/api/v1/model-configurations", json=duplicate_data, headers=auth_headers)
    assert response.status_code == 400

    error = response.json()
    assert "already exists" in error["error"]["message"].lower()

    # First configuration will be automatically cleaned up by test framework due to "Test" in name


async def test_create_configuration_invalid_provider(client, db, auth_headers):
    """Test creating configuration with invalid provider ID."""
    invalid_data = {
        **CONFIG_DATA,
        "name": "Invalid Provider Config",
        "llm_provider_id": "invalid-provider-id",
        "model_name": "some-model",
        "created_by": "test-user",
    }

    response = await client.post("/api/v1/model-configurations", json=invalid_data, headers=auth_headers)
    assert response.status_code == 400


async def test_delete_model_configurations(client, db, auth_headers):
    """Test deleting model configurations."""
    # Create test dependencies and a configuration to delete
    provider_id, model_id, prompt_id, kb_id = await _create_test_dependencies(client, auth_headers)

    # Create a test configuration
    config_data = {
        **CONFIG_DATA,
        "name": "Test Delete Model Configuration",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "created_by": "test-user",
    }

    create_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert create_response.status_code == 201
    config_id = extract_data(create_response)["id"]

    # Test deletion
    response = await client.delete(f"/api/v1/model-configurations/{config_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify deletion
    response = await client.get(f"/api/v1/model-configurations/{config_id}", headers=auth_headers)
    assert response.status_code == 404

    # Dependencies will be automatically cleaned up by test framework due to "Test" in name


async def test_cleanup_test_dependencies(client, db, auth_headers):
    """Clean up test dependencies - handled automatically by test framework."""
    # This test is no longer needed since cleanup is handled automatically
    # by the test framework based on "Test" naming convention
    pass


class ModelConfigurationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Model Configuration functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all model configuration test functions."""
        return [
            test_setup_test_dependencies,
            test_create_model_configuration_minimal,
            test_create_model_configuration_with_prompt,
            test_create_model_configuration_full,
            test_list_model_configurations,
            test_regular_user_sees_only_active_configurations,
            test_regular_user_listing_with_kb_does_not_expose_relationships,
            test_get_model_configuration_by_id,
            test_update_model_configuration,
            test_toggle_off_side_call_clears_config,
            test_filter_configurations_by_active_status,
            test_search_configurations_by_name,
            test_create_configuration_duplicate_name,
            test_create_configuration_invalid_provider,
            test_delete_model_configurations,
            test_cleanup_test_dependencies,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Model Configuration Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Integration tests for model configuration CRUD operations and relationships"


if __name__ == "__main__":
    suite = ModelConfigurationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
