"""
Comprehensive Integration Tests for Chat functionality.

These tests cover real-world scenarios and end-to-end functionality:
- Model Configuration integration with chat
- RAG functionality with knowledge bases
- LLM integration with proper mocking
- Streaming functionality (Server-Sent Events)
- Error handling and edge cases
- Performance and concurrent scenarios
- Authentication and permission boundaries
"""

import asyncio
import sys
import time

# Test Data for Comprehensive Scenarios
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.api_helpers import process_streaming_result
from integ.response_utils import extract_data
from shu.services.providers.adapter_base import (
    ProviderContentDeltaEventResult,
    ProviderFinalEventResult,
)


def get_unique_test_data():
    """Generate unique test data to avoid conflicts between test runs."""
    unique_id = str(uuid.uuid4())[:8]
    timestamp = str(int(time.time()))[-6:]

    return {
        "provider": {
            "name": f"Test OpenAI Provider {unique_id}",
            "provider_type": "local",
            "api_endpoint": "https://api.openai.com/v1",
            "api_key": f"test-key-comprehensive-{timestamp}",
            "is_active": True,
        },
        "model_config": {
            "name": f"Research Assistant {unique_id}",
            "description": "AI assistant for research tasks with biology knowledge",
            "model_name": "gpt-4",
            "is_active": True,
            "created_by": "test-user",
        },
        "prompt": {
            "name": f"Research Assistant Prompt {unique_id}",
            "content": "You are a helpful research assistant specializing in biology and life sciences. Use the provided context to answer questions accurately and cite your sources.",
            "entity_type": "model_configuration",
        },
        "knowledge_base": {
            "name": f"Test Biology Research KB {unique_id}",  # Follows test naming convention
            "description": "Test knowledge base containing biology research papers and documents",
        },
    }


MOCK_LLM_RESPONSES = {
    "simple": "This is a test response from the LLM.",
    "with_rag": "Based on the provided research context, the answer is that cells are the basic unit of life. This information comes from the biology textbook in your knowledge base.",
    "streaming": ["This ", "is ", "a ", "streaming ", "response ", "from ", "the ", "LLM."],
}


async def test_model_configuration_conversation_creation(client, db, auth_headers):
    """Test creating conversation with model configuration - comprehensive scenario."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    print(f"Provider response: {provider_response.status_code} - {provider_response.text}")
    assert provider_response.status_code == 201  # Providers return 201 on creation
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    print(f"Model response: {model_response.status_code} - {model_response.text}")
    assert model_response.status_code == 200  # Model creation returns 200

    # 3. Create knowledge base
    kb_response = await client.post("/api/v1/knowledge-bases", json=test_data["knowledge_base"], headers=auth_headers)
    print(f"KB response: {kb_response.status_code} - {kb_response.text}")
    assert kb_response.status_code == 201
    kb_data = kb_response.json()
    kb_id = kb_data["data"]["id"]  # KB API returns data wrapped in "data" field

    # 3. Create model configuration (without prompt first, then add prompt later if needed)
    model_config_data = {
        **test_data["model_config"],
        "llm_provider_id": provider_id,
        "knowledge_base_ids": [kb_id],
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    print(f"Model config response: {config_response.status_code} - {config_response.text}")
    assert config_response.status_code == 201
    config_data = config_response.json()
    model_config_id = config_data["data"]["id"]  # Model config API returns data wrapped in "data" field

    # 4. Create conversation with model configuration
    conversation_data = {
        "title": "Research Chat Session",
        "model_configuration_id": model_config_id,
    }

    response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    print(f"Conversation response: {response.status_code} - {response.text}")
    assert response.status_code == 200

    result = extract_data(response)

    # 5. Verify basic conversation structure (relaxed assertions for initial testing)
    assert result["model_configuration_id"] == model_config_id
    assert result["title"] == "Research Chat Session"
    assert result["is_active"] is True
    assert "id" in result
    assert "user_id" in result
    assert "created_at" in result

    # 6. Verify model configuration is included (if available)
    if result.get("model_configuration"):
        assert test_data["model_config"]["name"] in result["model_configuration"]["name"]
        # Only check KB attachment if the response includes it
        if "has_knowledge_bases" in result["model_configuration"]:
            assert result["model_configuration"]["has_knowledge_bases"] is True

    # 7. Verify database relationships
    db_result = await db.execute(
        text("SELECT model_configuration_id, title FROM conversations WHERE id = :id"),
        {"id": result["id"]},
    )
    conversation_row = db_result.fetchone()
    assert conversation_row.model_configuration_id == model_config_id
    assert conversation_row.title == "Research Chat Session"

    return {
        "conversation_id": result["id"],
        "model_config_id": model_config_id,
        "provider_id": provider_id,
        "kb_id": kb_id,
    }


async def test_rag_integration_with_model_config(client, db, auth_headers):
    """Test basic message sending with model configuration (simplified RAG test)."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider first
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    assert provider_response.status_code == 201
    provider_id = extract_data(provider_response)["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code == 200  # Model creation returns 200

    # 3. Create model configuration (required for conversation creation)
    model_config_data = {
        "name": "Simple Test Config",
        "description": "Basic config for message testing",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # 3. Create conversation with model configuration (now required)
    conversation_data = {"title": "Simple Message Test", "model_configuration_id": model_config_id}
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    print(f"Conversation creation response: {conv_response.status_code} - {conv_response.text}")
    assert conv_response.status_code == 200, f"Expected 200, got {conv_response.status_code}: {conv_response.text}"
    conversation_id = extract_data(conv_response)["id"]

    # Mock LLM response to avoid actual LLM calls
    with (
        patch("shu.llm.service.LLMService.get_client") as mock_client,
        patch("shu.llm.service.LLMService.get_model_by_id") as mock_get_model,
        patch("shu.llm.service.LLMService.get_active_providers") as mock_providers,
    ):
        mock_llm_client = AsyncMock()
        # Provide a minimal provider stub so adapter lookup succeeds
        mock_llm_client.provider = SimpleNamespace(
            provider_definition=SimpleNamespace(provider_adapter_name="local"),
            supports_streaming=True,
            api_key_encrypted=None,
            api_endpoint="http://localhost:11434",
            config={},
            provider_type="local",
        )

        async def fake_stream():
            yield ProviderFinalEventResult(
                content="This is a test response from the mocked LLM.",
                type="final_message",
                metadata={"usage": {}},
            )

        mock_llm_client.chat_completion.return_value = fake_stream()
        mock_client.return_value = mock_llm_client

        # Mock the get_model_by_id to return a valid model
        mock_model = AsyncMock()
        mock_model.id = "test-model-id"
        mock_model.model_name = "gpt-4"
        mock_model.is_active = True
        mock_get_model.return_value = mock_model

        # Mock get_active_providers to return a default provider
        mock_provider = AsyncMock()
        mock_provider.id = "test-provider-id"
        mock_provider.name = "Test Provider"
        mock_provider.models = [mock_model]
        mock_providers.return_value = [mock_provider]

        # Send simple message
        message_data = {
            "message": "Hello, this is a test message",
            "rag_rewrite_mode": "no_rag",
        }

        response = await client.post(
            f"/api/v1/chat/conversations/{conversation_id}/send",
            json=message_data,
            headers=auth_headers,
        )

        # Check if the response is successful or has expected error
        print(f"Message response: {response.status_code} - {response.text}")

        # For now, accept either success or specific error types
        assert response.status_code == 200, f"Unexpected status code: {response.status_code}"

        result = await process_streaming_result(response)
        assert "content" in result, result
        assert result["content"] == "This is a test response from the mocked LLM.", f"Wrong content: {result}"
        return result


async def test_llm_integration_with_error_handling(client, db, auth_headers):
    """Test LLM integration with proper error handling scenarios."""
    print("Starting error handling test...")

    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()
    print(f"Generated test data: {test_data}")

    # 1. Create LLM provider with invalid API key to test error handling
    provider_data = {
        "name": f"Error Test Provider {test_data['provider']['name'][-8:]}",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "invalid-key-for-testing",  # Invalid key to trigger auth errors
        "is_active": True,
    }
    print(f"Creating provider with data: {provider_data}")

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    print(f"Provider response: {provider_response.status_code} - {provider_response.text}")
    assert provider_response.status_code == 201, f"Provider creation failed: {provider_response.text}"
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Error Test",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    print(f"Model response: {model_response.status_code} - {model_response.text}")
    assert model_response.status_code in [200, 201], f"Model creation failed: {model_response.text}"
    model_data_response = model_response.json()

    # 3. Create knowledge base (required for model configuration)
    kb_response = await client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": f"Error Test KB {test_data['provider']['name'][-8:]}",
            "description": "Test knowledge base for error handling",
        },
        headers=auth_headers,
    )
    print(f"KB response: {kb_response.status_code} - {kb_response.text}")
    assert kb_response.status_code == 201, f"KB creation failed: {kb_response.text}"
    kb_data = kb_response.json()
    kb_id = kb_data["data"]["id"]

    # 4. Create model configuration
    model_config_data = {
        "name": f"Error Test Assistant {test_data['provider']['name'][-8:]}",
        "description": "Assistant for testing error handling",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": [kb_id],
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    print(f"Model config response: {config_response.status_code} - {config_response.text}")
    assert config_response.status_code == 201, f"Model config creation failed: {config_response.text}"
    model_config_id = extract_data(config_response)["id"]

    # 4. Create conversation with model configuration
    conversation_data = {"title": "Error Handling Test", "model_configuration_id": model_config_id}

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]

    # 5. Test LLM authentication error handling
    message_data = {
        "message": "This should trigger an authentication error",
        "rag_rewrite_mode": "no_rag",
    }

    response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=message_data,
        headers=auth_headers,
    )

    print(f"Auth error test response: {response.status_code} - {response.text}")

    # Should handle authentication error gracefully
    assert response.status_code == 200, f"Unexpected status code: {response.status_code} {response.text}"

    content = await process_streaming_result(response)
    assert "The request failed. You may want to try another model." in content, (
        f"Expected error message, got: {content}"
    )

    # 6. Verify conversation and message were still created in database
    db_result = await db.execute(text("SELECT id, title FROM conversations WHERE id = :id"), {"id": conversation_id})
    conversation_row = db_result.fetchone()
    assert conversation_row is not None, "Conversation should exist in database even after LLM error"
    assert conversation_row.title == "Error Handling Test"

    return {
        "conversation_id": conversation_id,
        "model_config_id": model_config_id,
        "provider_id": provider_id,
        "kb_id": kb_id,
    }


async def test_streaming_functionality(client, db, auth_headers):
    """Test Server-Sent Events streaming functionality."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    assert provider_response.status_code == 201
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Streaming",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code in [200, 201]

    # 3. Create model configuration
    model_config_data = {
        "name": f"Streaming Test Assistant {test_data['provider']['name'][-8:]}",
        "description": "Assistant for testing streaming functionality",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # 4. Create conversation with model configuration
    conversation_data = {"title": "Streaming Test", "model_configuration_id": model_config_id}
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]

    # 5. Test streaming endpoint (without complex mocking - test real integration)
    message_data = {
        "message": "Stream this response",
        "rag_rewrite_mode": "no_rag",
    }

    with patch("shu.llm.service.LLMService.get_client") as mock_client:
        mock_llm_client = AsyncMock()
        mock_llm_client.provider = SimpleNamespace(
            provider_definition=SimpleNamespace(provider_adapter_name="local"),
            supports_streaming=True,
            api_key_encrypted=None,
            api_endpoint="http://localhost:11434",
            config={},
            provider_type="local",
        )

        async def fake_stream():
            # Simulate streaming chunks and final message
            yield ProviderContentDeltaEventResult(content="Echo: ")
            yield ProviderContentDeltaEventResult(content="Stream ")
            yield ProviderContentDeltaEventResult(content="this ")
            yield ProviderContentDeltaEventResult(content="response")
            yield ProviderFinalEventResult(content="Echo: Stream this response", metadata={"usage": {}})

        mock_llm_client.chat_completion.return_value = fake_stream()
        mock_client.return_value = mock_llm_client

        response = await client.post(
            f"/api/v1/chat/conversations/{conversation_id}/send",
            json=message_data,
            headers=auth_headers,
        )

        print(
            f"Streaming response: {response.status_code} - Headers: {dict(response.headers)} - Content: {response.text}"
        )

        assert response.status_code == 200, f"Unexpected status code: {response.status_code}"

        result = await process_streaming_result(response)
        assert "content" in result, result
        assert result["content"] == "Echo: Stream this response", result

    return {
        "conversation_id": conversation_id,
        "model_config_id": model_config_id,
        "provider_id": provider_id,
    }


async def test_performance_concurrent_conversations(client, db, auth_headers):
    """Test performance with multiple concurrent conversations."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider and model configuration (shared for all conversations)
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    assert provider_response.status_code == 201
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Performance",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code in [200, 201]

    # 3. Create model configuration
    model_config_data = {
        "name": f"Performance Test Assistant {test_data['provider']['name'][-8:]}",
        "description": "Assistant for testing concurrent performance",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # 4. Test concurrent conversation creation
    conversation_tasks = []
    for i in range(3):  # Reduced from 5 to 3 for faster testing
        conversation_data = {
            "title": f"Performance Test Chat {i + 1}",
            "model_configuration_id": model_config_id,
        }
        task = client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
        conversation_tasks.append(task)

    # Execute all conversation creations concurrently
    start_time = time.time()
    responses = await asyncio.gather(*conversation_tasks)
    creation_time = time.time() - start_time

    # Verify all conversations were created successfully
    conversation_ids = []
    for response in responses:
        assert response.status_code == 200, f"Conversation creation failed: {response.text}"
        conversation_ids.append(extract_data(response)["id"])

    # 5. Test basic performance metrics (without complex mocking)
    # Verify performance is reasonable (adjust thresholds as needed)
    assert creation_time < 5.0, f"Conversation creation took too long: {creation_time}s"
    assert len(conversation_ids) == 3, f"Expected 3 conversations, got {len(conversation_ids)}"

    print(f"Performance test results: {len(conversation_ids)} conversations created in {creation_time:.3f}s")

    return {
        "creation_time": creation_time,
        "conversations_created": len(conversation_ids),
        "model_config_id": model_config_id,
        "provider_id": provider_id,
        "conversation_ids": conversation_ids,
    }


async def test_authentication_and_permissions(client, db, auth_headers):
    """Test authentication boundaries and user isolation."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider and model configuration
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    assert provider_response.status_code == 201
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Auth Test",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code in [200, 201]

    # 3. Create model configuration
    model_config_data = {
        "name": f"Auth Test Assistant {test_data['provider']['name'][-8:]}",
        "description": "Assistant for testing authentication",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # 4. Create conversation with authenticated user
    conversation_data = {
        "title": "Auth Test Conversation",
        "model_configuration_id": model_config_id,
    }
    response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert response.status_code == 200
    conversation_id = extract_data(response)["id"]

    # Test 1: Access without authentication
    no_auth_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}")
    assert no_auth_response.status_code == 401, "Should require authentication"

    # Test 2: Invalid authentication token
    invalid_headers = {"Authorization": "Bearer invalid-token"}
    invalid_auth_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=invalid_headers)
    assert invalid_auth_response.status_code == 401, "Should reject invalid token"

    # Test 3: Access to non-existent conversation
    fake_id = "00000000-0000-0000-0000-000000000000"
    not_found_response = await client.get(f"/api/v1/chat/conversations/{fake_id}", headers=auth_headers)
    assert not_found_response.status_code == 404, "Should return 404 for non-existent conversation"

    # Test 4: Verify user can access their own conversation
    own_conversation_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth_headers)
    assert own_conversation_response.status_code == 200, "Should allow access to own conversation"

    return {
        "conversation_id": conversation_id,
        "model_config_id": model_config_id,
        "provider_id": provider_id,
    }


async def test_edge_cases_and_validation(client, db, auth_headers):
    """Test edge cases and input validation."""
    # Get unique test data to avoid conflicts
    test_data = get_unique_test_data()

    # 1. Create LLM provider and model configuration
    provider_response = await client.post("/api/v1/llm/providers", json=test_data["provider"], headers=auth_headers)
    assert provider_response.status_code == 201
    provider_data = extract_data(provider_response)
    provider_id = provider_data["id"]

    # 2. Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Edge Test",
        "model_type": "chat",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "cost_per_input_token": 0.00003,
        "cost_per_output_token": 0.00006,
        "is_active": True,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code in [200, 201]

    # 3. Create model configuration
    model_config_data = {
        "name": f"Edge Test Assistant {test_data['provider']['name'][-8:]}",
        "description": "Assistant for testing edge cases",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # 4. Create conversation
    conversation_data = {"title": "Edge Case Test", "model_configuration_id": model_config_id}
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200, f"Conversation creation failed: {conv_response.text}"
    conversation_id = extract_data(conv_response)["id"]

    # Test 1: Empty message validation
    empty_message_data = {
        "message": "",
        "rag_rewrite_mode": "no_rag",
    }

    response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=empty_message_data,
        headers=auth_headers,
    )
    print(f"Empty message response: {response.status_code} - {response.text}")

    # Should either reject empty message or handle gracefully
    assert response.status_code in [400, 422, 500], "Should handle empty message appropriately"

    if response.status_code in [400, 422]:
        # Verify the error message indicates validation issue
        error_data = response.json()
        error_message = error_data.get("error", {}).get("message", "").lower()
        assert any(keyword in error_message for keyword in ["empty", "required", "invalid"]), (
            f"Expected validation error, got: {error_message}"
        )

    # Test 2: Very long message (simplified - no complex mocking)
    long_message_data = {
        "message": "This is a test of a reasonably long message to verify the system can handle longer inputs without issues. "
        * 10,  # Reasonable length
        "rag_rewrite_mode": "no_rag",
    }

    long_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=long_message_data,
        headers=auth_headers,
    )
    print(f"Long message response: {long_response.status_code} - {long_response.text[:200]}")

    # Should handle long messages gracefully (may fail due to invalid API key, which is expected)
    assert long_response.status_code in [
        200,
        422,
        413,
        500,
    ], "Should handle long messages gracefully"

    # Test 3: No extra conversation parameters
    request = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json={"message": "", "rag_rewrite_mode": "no_rag", "temperature": 0.7, "max_tokens": 50},
        headers=auth_headers,
    )
    assert request.status_code == 422, "Should reject additional conversation parameters"
    assert request.json()["detail"][0]["msg"] == "Extra inputs are not permitted", (
        "Should reject additional conversation parameters"
    )

    return {
        "conversation_id": conversation_id,
        "model_config_id": model_config_id,
        "provider_id": provider_id,
    }


class ChatComprehensiveIntegrationTestSuite(BaseIntegrationTestSuite):
    def get_suite_name(self) -> str:
        return "Chat Comprehensive Integration"

    def get_suite_description(self) -> str:
        return "Comprehensive integration tests for chat functionality including model configs, RAG, LLM integration, and streaming"

    def get_test_functions(self) -> list[Callable]:
        return [
            test_model_configuration_conversation_creation,
            test_rag_integration_with_model_config,
            test_llm_integration_with_error_handling,
            test_streaming_functionality,
            test_performance_concurrent_conversations,
            test_authentication_and_permissions,
            test_edge_cases_and_validation,
        ]


if __name__ == "__main__":
    import asyncio

    suite = ChatComprehensiveIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys

    sys.exit(exit_code)
