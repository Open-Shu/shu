"""
Model Configuration KB Prompt Assignment Integration Tests

These tests verify the new model configuration-level KB prompt assignment system:
- KB prompt assignment/removal via model configurations
- Multiple KBs with different prompts per model configuration
- Backward compatibility with legacy KB prompts in chat
- API endpoints for KB prompt management
- Data persistence and retrieval
"""

import sys
import os
import uuid
from typing import List, Callable
from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data


# Test Data Constants
PROVIDER_DATA = {
    "name": "Test KB Prompt Provider",
    "provider_type": "openai", 
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-kb-prompt-key",
    "is_active": True
}

MODEL_DATA = {
    "model_name": "test-gpt-4o-kb-prompts",
    "display_name": "Test GPT-4o KB Prompts",
    "model_type": "chat",
    "context_window": 128000,
    "max_output_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": True,
    "cost_per_input_token": 0.000005,
    "cost_per_output_token": 0.000015,
    "is_active": True
}

KB_DATA_1 = {
    "name": "Test KB 1 for Prompts",
    "description": "First test knowledge base for prompt assignments",
    "sync_enabled": True,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "chunk_size": 1000,
    "chunk_overlap": 200
}

KB_DATA_2 = {
    "name": "Test KB 2 for Prompts", 
    "description": "Second test knowledge base for prompt assignments",
    "sync_enabled": True,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "chunk_size": 1000,
    "chunk_overlap": 200
}

MAIN_PROMPT_DATA = {
    "name": "Test Main Prompt",
    "description": "Main prompt for model configuration",
    "content": "You are a helpful AI assistant. Answer questions based on the provided context.",
    "entity_type": "llm_model",
    "is_active": True
}

KB_PROMPT_DATA_1 = {
    "name": "Test KB Prompt 1",
    "description": "Specific prompt for KB 1",
    "content": "You are a research assistant. Focus on technical details when answering from this knowledge base.",
    "entity_type": "knowledge_base",  # Restored to knowledge_base for semantic correctness
    "is_active": True
}

KB_PROMPT_DATA_2 = {
    "name": "Test KB Prompt 2",
    "description": "Specific prompt for KB 2",
    "content": "You are a friendly tutor. Explain concepts simply when answering from this knowledge base.",
    "entity_type": "knowledge_base",  # Restored to knowledge_base for semantic correctness
    "is_active": True
}


# Standardized helper now provided by tests.response_utils.extract_data


# Test Functions
async def test_model_config_kb_prompt_health_check(client, db, auth_headers):
    """Test that model configuration KB prompt endpoints are accessible."""
    # Create minimal test data
    unique_id = str(uuid.uuid4())[:8]
    
    # Create provider and model
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    print(f"Provider response: {provider_response.status_code} - {provider_response.text}")
    assert provider_response.status_code == 201, f"Provider creation failed: {provider_response.status_code}: {provider_response.text}"
    provider = extract_data(provider_response)
    provider_id = provider["id"]
    
    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    print(f"Model response: {model_response.status_code} - {model_response.text}")
    assert model_response.status_code == 200, f"Model creation failed: {model_response.status_code}: {model_response.text}"
    
    # Create model configuration
    config_data = {
        "name": f"Test Config {unique_id}",
        "description": "Test configuration for KB prompt health check",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-user"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    print(f"Config response: {config_response.status_code} - {config_response.text}")
    assert config_response.status_code == 201, f"Config creation failed: {config_response.status_code}: {config_response.text}"
    config = extract_data(config_response)
    config_id = config["id"]
    
    # Test KB prompts endpoint accessibility
    response = await client.get(f"/api/v1/model-configurations/{config_id}/kb-prompts", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    
    # Should return empty dict initially
    data = extract_data(response)
    assert isinstance(data, dict)
    assert len(data) == 0


async def test_assign_kb_prompt_to_model_config(client, db, auth_headers):
    """Test assigning a KB-specific prompt to a model configuration."""
    unique_id = str(uuid.uuid4())[:8]
    
    # Create provider and model
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider = extract_data(provider_response)
    provider_id = provider["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)

    # Create KB
    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb = extract_data(kb_response)
    kb_id = kb["id"]

    # Create KB prompt
    kb_prompt_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=kb_prompt_data, headers=auth_headers)
    prompt = extract_data(prompt_response)
    prompt_id = prompt["id"]
    
    # Create model configuration with KB
    config_data = {
        "name": f"Test Config {unique_id}",
        "description": "Test configuration for KB prompt assignment",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],
        "is_active": True,
        "created_by": "test-user"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    config = extract_data(config_response)
    config_id = config["id"]
    
    # Assign KB prompt to model configuration
    assignment_data = {
        "knowledge_base_id": kb_id,
        "prompt_id": prompt_id
    }
    
    response = await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json=assignment_data,
        headers=auth_headers
    )
    assert response.status_code == 201, f"Assignment failed: {response.status_code}: {response.text}"
    
    # Verify assignment was created
    assignment = extract_data(response)
    assert assignment["model_configuration_id"] == config_id
    assert assignment["knowledge_base_id"] == kb_id
    assert assignment["prompt_id"] == prompt_id
    assert assignment["is_active"] == True
    
    # Verify assignment exists in database
    result = await db.execute(text(
        "SELECT * FROM model_configuration_kb_prompts WHERE model_configuration_id = :config_id AND knowledge_base_id = :kb_id"
    ), {"config_id": config_id, "kb_id": kb_id})
    db_assignment = result.fetchone()
    assert db_assignment is not None
    assert db_assignment.prompt_id == prompt_id
    assert db_assignment.is_active == True


async def test_get_model_config_kb_prompts(client, db, auth_headers):
    """Test retrieving KB prompts for a model configuration."""
    unique_id = str(uuid.uuid4())[:8]
    
    # Create test data (provider, model, KBs, prompts, config)
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    
    # Create two KBs
    kb1_data = {**KB_DATA_1, "name": f"Test KB 1 {unique_id}"}
    kb1_response = await client.post("/api/v1/knowledge-bases", json=kb1_data, headers=auth_headers)
    kb1_id = extract_data(kb1_response)["id"]

    kb2_data = {**KB_DATA_2, "name": f"Test KB 2 {unique_id}"}
    kb2_response = await client.post("/api/v1/knowledge-bases", json=kb2_data, headers=auth_headers)
    kb2_id = extract_data(kb2_response)["id"]

    # Create two KB prompts
    prompt1_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt 1 {unique_id}"}
    prompt1_response = await client.post("/api/v1/prompts/", json=prompt1_data, headers=auth_headers)
    prompt1_id = extract_data(prompt1_response)["id"]

    prompt2_data = {**KB_PROMPT_DATA_2, "name": f"Test KB Prompt 2 {unique_id}"}
    prompt2_response = await client.post("/api/v1/prompts/", json=prompt2_data, headers=auth_headers)
    prompt2_id = extract_data(prompt2_response)["id"]

    # Create model configuration with both KBs
    config_data = {
        "name": f"Test Config {unique_id}",
        "description": "Test configuration for multiple KB prompts",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb1_id, kb2_id],
        "is_active": True,
        "created_by": "test-user"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    config_id = extract_data(config_response)["id"]

    # Assign prompts to both KBs
    await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json={"knowledge_base_id": kb1_id, "prompt_id": prompt1_id},
        headers=auth_headers
    )
    
    await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json={"knowledge_base_id": kb2_id, "prompt_id": prompt2_id},
        headers=auth_headers
    )
    
    # Get KB prompts
    response = await client.get(f"/api/v1/model-configurations/{config_id}/kb-prompts", headers=auth_headers)
    assert response.status_code == 200
    
    kb_prompts = extract_data(response)
    assert isinstance(kb_prompts, dict)
    assert len(kb_prompts) == 2
    
    # Verify KB 1 prompt
    assert kb1_id in kb_prompts
    kb1_prompt = kb_prompts[kb1_id]
    assert kb1_prompt["knowledge_base"]["id"] == kb1_id
    assert kb1_prompt["prompt"]["id"] == prompt1_id
    assert kb1_prompt["prompt"]["content"] == prompt1_data["content"]
    
    # Verify KB 2 prompt  
    assert kb2_id in kb_prompts
    kb2_prompt = kb_prompts[kb2_id]
    assert kb2_prompt["knowledge_base"]["id"] == kb2_id
    assert kb2_prompt["prompt"]["id"] == prompt2_id
    assert kb2_prompt["prompt"]["content"] == prompt2_data["content"]


async def test_remove_kb_prompt_from_model_config(client, db, auth_headers):
    """Test removing a KB prompt assignment from a model configuration."""
    unique_id = str(uuid.uuid4())[:8]

    # Create test data and assign KB prompt (reuse logic from previous tests)
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)

    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    kb_prompt_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=kb_prompt_data, headers=auth_headers)
    prompt_id = extract_data(prompt_response)["id"]

    config_data = {
        "name": f"Test Config {unique_id}",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],
        "is_active": True,
        "created_by": "test-user"
    }

    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    config_id = extract_data(config_response)["id"]

    # Assign KB prompt
    await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json={"knowledge_base_id": kb_id, "prompt_id": prompt_id},
        headers=auth_headers
    )

    # Remove KB prompt assignment
    response = await client.delete(
        f"/api/v1/model-configurations/{config_id}/kb-prompts/{kb_id}",
        headers=auth_headers
    )
    assert response.status_code == 200, f"Removal failed: {response.status_code}: {response.text}"

    result = extract_data(response)
    assert result["removed"] == True

    # Verify assignment is deactivated in database
    db_result = await db.execute(text(
        "SELECT is_active FROM model_configuration_kb_prompts WHERE model_configuration_id = :config_id AND knowledge_base_id = :kb_id"
    ), {"config_id": config_id, "kb_id": kb_id})
    assignment = db_result.fetchone()
    assert assignment is not None
    assert assignment.is_active == False

    # Verify GET endpoint no longer returns the assignment
    get_response = await client.get(f"/api/v1/model-configurations/{config_id}/kb-prompts", headers=auth_headers)
    kb_prompts = extract_data(get_response)
    assert kb_id not in kb_prompts


async def test_update_kb_prompt_assignment(client, db, auth_headers):
    """Test updating a KB prompt assignment (changing to different prompt)."""
    unique_id = str(uuid.uuid4())[:8]

    # Create test data
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)

    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    # Create two different prompts
    prompt1_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt 1 {unique_id}"}
    prompt1_response = await client.post("/api/v1/prompts/", json=prompt1_data, headers=auth_headers)
    prompt1_id = extract_data(prompt1_response)["id"]

    prompt2_data = {**KB_PROMPT_DATA_2, "name": f"Test KB Prompt 2 {unique_id}"}
    prompt2_response = await client.post("/api/v1/prompts/", json=prompt2_data, headers=auth_headers)
    prompt2_id = extract_data(prompt2_response)["id"]

    config_data = {
        "name": f"Test Config {unique_id}",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],
        "is_active": True,
        "created_by": "test-user"
    }

    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    config_id = extract_data(config_response)["id"]

    # Assign first prompt
    await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json={"knowledge_base_id": kb_id, "prompt_id": prompt1_id},
        headers=auth_headers
    )

    # Update to second prompt (should replace the first)
    response = await client.post(
        f"/api/v1/model-configurations/{config_id}/kb-prompts",
        json={"knowledge_base_id": kb_id, "prompt_id": prompt2_id},
        headers=auth_headers
    )
    assert response.status_code == 201

    # Verify the assignment now uses the second prompt
    get_response = await client.get(f"/api/v1/model-configurations/{config_id}/kb-prompts", headers=auth_headers)
    kb_prompts = extract_data(get_response)

    assert kb_id in kb_prompts
    assert kb_prompts[kb_id]["prompt"]["id"] == prompt2_id
    assert kb_prompts[kb_id]["prompt"]["content"] == prompt2_data["content"]


async def test_model_config_with_kb_prompts_in_create(client, db, auth_headers):
    """Test creating a model configuration with KB prompt assignments in the initial request."""
    unique_id = str(uuid.uuid4())[:8]

    # Create test data
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)

    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    kb_prompt_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=kb_prompt_data, headers=auth_headers)
    prompt_id = extract_data(prompt_response)["id"]

    # Create model configuration with KB prompt assignments
    config_data = {
        "name": f"Test Config {unique_id}",
        "description": "Test configuration with initial KB prompts",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],
        "kb_prompt_assignments": [
            {
                "knowledge_base_id": kb_id,
                "prompt_id": prompt_id
            }
        ],
        "is_active": True,
        "created_by": "test-user"
    }

    response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert response.status_code == 201, f"Creation failed: {response.status_code}: {response.text}"

    config_id = extract_data(response)["id"]

    # Verify KB prompt assignment was created
    get_response = await client.get(f"/api/v1/model-configurations/{config_id}/kb-prompts", headers=auth_headers)
    kb_prompts = extract_data(get_response)

    assert kb_id in kb_prompts
    assert kb_prompts[kb_id]["prompt"]["id"] == prompt_id


async def test_legacy_kb_prompt_assignment_blocked(client, db, auth_headers):
    """Test that legacy direct KB prompt assignments are now blocked."""
    unique_id = str(uuid.uuid4())[:8]

    # Create KB and prompt
    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    kb_prompt_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=kb_prompt_data, headers=auth_headers)
    prompt_id = extract_data(prompt_response)["id"]

    # Attempt legacy direct KB prompt assignment (should fail)
    assignment_data = {
        "entity_id": kb_id,
        "entity_type": "knowledge_base",
        "is_active": True
    }

    response = await client.post(
        f"/api/v1/prompts/{prompt_id}/assignments",
        json=assignment_data,
        headers=auth_headers
    )

    # Should fail (422) since direct assignment to knowledge bases is now blocked
    # The new architecture requires using model configuration assignments instead
    assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
    error_response = response.json()
    assert "error" in error_response
    assert "Direct assignment to knowledge bases is not supported" in error_response["error"]["message"]


async def test_model_config_response_includes_kb_prompts(client, db, auth_headers):
    """Test that model configuration responses include KB prompt information."""
    unique_id = str(uuid.uuid4())[:8]

    # Create test data
    provider_data = {**PROVIDER_DATA, "name": f"Test Provider {unique_id}"}
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {**MODEL_DATA, "model_name": f"test-model-{unique_id}"}
    await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)

    kb_data = {**KB_DATA_1, "name": f"Test KB {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    kb_prompt_data = {**KB_PROMPT_DATA_1, "name": f"Test KB Prompt {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=kb_prompt_data, headers=auth_headers)
    prompt_id = extract_data(prompt_response)["id"]

    # Create model configuration with KB and prompt
    config_data = {
        "name": f"Test Config {unique_id}",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],
        "kb_prompt_assignments": [
            {
                "knowledge_base_id": kb_id,
                "prompt_id": prompt_id
            }
        ],
        "is_active": True,
        "created_by": "test-user"
    }

    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    config_id = extract_data(config_response)["id"]

    # Get model configuration and verify KB prompts are included
    get_response = await client.get(f"/api/v1/model-configurations/{config_id}", headers=auth_headers)
    assert get_response.status_code == 200

    config = extract_data(get_response)

    # Verify kb_prompts field exists and contains the assignment
    assert "kb_prompts" in config
    assert isinstance(config["kb_prompts"], dict)
    assert kb_id in config["kb_prompts"]

    kb_prompt_info = config["kb_prompts"][kb_id]
    assert kb_prompt_info["id"] == prompt_id
    assert kb_prompt_info["name"] == kb_prompt_data["name"]
    assert kb_prompt_info["content"] == kb_prompt_data["content"]
    assert "assigned_at" in kb_prompt_info


# Test Suite Class
class ModelConfigKBPromptsIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for model configuration KB prompt assignments."""
    
    def get_test_functions(self) -> List[Callable]:
        return [
            test_model_config_kb_prompt_health_check,
            test_assign_kb_prompt_to_model_config,
            test_get_model_config_kb_prompts,
            test_remove_kb_prompt_from_model_config,
            test_update_kb_prompt_assignment,
            test_model_config_with_kb_prompts_in_create,
            test_legacy_kb_prompt_assignment_blocked,
            test_model_config_response_includes_kb_prompts,
        ]
    
    def get_suite_name(self) -> str:
        return "Model Config KB Prompts Integration"

    def get_suite_description(self) -> str:
        return "Tests for model configuration-level KB prompt assignment system"


# Entry point for running this test suite individually
if __name__ == "__main__":
    suite = ModelConfigKBPromptsIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
