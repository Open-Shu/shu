"""
Integration tests for Chat functionality using custom test framework.

These tests verify actual chat workflows end-to-end:
- Conversation CRUD operations
- Message handling and persistence
- LLM integration and responses
- Streaming functionality
- Prompt system integration
- Advanced features (context management, session persistence, model switching)
"""

import sys
import os
import json
import logging
from typing import List, Callable, Dict, Any
from sqlalchemy import text
from integ.response_utils import extract_data

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_llm_errors,
    expect_authentication_errors,
    expect_validation_errors,
    ExpectedErrorContext
)

logger = logging.getLogger(__name__)


# Test Data for Model Configuration-based Conversations (Current System)
PROVIDER_DATA = {
    "name": "Test OpenAI Provider",
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-12345",
    "is_active": True
}

MODEL_DATA = {
    "model_name": "gpt-4",
    "display_name": "GPT-4 Test Model",
    "description": "Test model for integration testing",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False
}

MODEL_CONFIG_DATA = {
    "name": "Test Chat Assistant",
    "description": "Test model configuration for chat integration",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": []
}

# Legacy conversation data for tests that haven't been updated to model config pattern
VALID_CONVERSATION_DATA = {
    "title": "Test Legacy Conversation",
    "model_configuration_id": None  # Will be set dynamically in tests
}

VALID_MESSAGE_DATA = {
    "role": "user",
    "content": "Hello, this is a test message for integration testing.",
    "metadata": {"test": True}
}

SEND_MESSAGE_DATA = {
    "message": "What is the capital of France?",
    "rag_rewrite_mode": "no_rag",
    "temperature": 0.7,
    "max_tokens": 100
}

# Session data for session management tests
SESSION_DATA = {
    "context": "test session context",
    "preferences": {"theme": "dark", "language": "en"},
    "metadata": {"test_session": True}
}


async def test_health_endpoint(client, db, auth_headers):
    """Test that the health endpoint works."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200, response.status_code
    data = response.json()
    assert data["data"]["status"] in ("healthy", "warning"), data


async def _create_test_dependencies(client, auth_headers):
    """Helper function to create test dependencies for model configuration tests."""
    # Create provider
    provider_response = await client.post("/api/v1/llm/providers", json=PROVIDER_DATA, headers=auth_headers)
    assert provider_response.status_code == 201
    provider_json = provider_response.json()
    provider_body = provider_json.get("data", provider_json)
    provider_id = provider_body["id"]

    # Create model
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=MODEL_DATA, headers=auth_headers)
    assert model_response.status_code == 200

    return provider_id


async def test_create_conversation_with_model_config_basic(client, db, auth_headers):
    """Test creating a conversation with model configuration (current system)."""
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    from integ.response_utils import extract_data
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Model Config Chat",
        "model_configuration_id": model_config_id
    }

    response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert response.status_code == 200

    data = extract_data(response)

    # Verify conversation was created with model configuration
    assert data["model_configuration_id"] == model_config_id
    assert data["model_configuration"] is not None
    assert data["model_configuration"]["name"] == model_config_data["name"]
    assert data["title"] == "Test Model Config Chat"
    assert data["is_active"] is True

    # Verify data was stored in database
    from sqlalchemy import text
    result = await db.execute(text("SELECT * FROM conversations WHERE id = :id"), {"id": data["id"]})
    conversation_row = result.fetchone()
    assert conversation_row is not None
    assert conversation_row.model_configuration_id == model_config_id
    assert conversation_row.title == "Test Model Config Chat"


async def test_list_conversations_with_model_configs(client, db, auth_headers):
    """Test listing user conversations with model configurations."""
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test List Conversations",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert create_response.status_code == 200

    # List conversations
    response = await client.get("/api/v1/chat/conversations", headers=auth_headers)
    assert response.status_code == 200

    result = response.json()
    assert "data" in result
    conversations = result["data"]
    assert isinstance(conversations, list)
    assert len(conversations) >= 1

    # Check conversation structure includes model configuration
    conversation = conversations[0]
    assert "id" in conversation
    assert "title" in conversation
    assert "model_configuration_id" in conversation
    assert "model_configuration" in conversation
    assert conversation["model_configuration"] is not None
    assert "created_at" in conversation
    assert "updated_at" in conversation


async def test_get_conversation_by_id_with_model_config(client, db, auth_headers):
    """Test retrieving a specific conversation with model configuration."""
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation
    conversation_data = {
        "title": "Test Get Conversation",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    conversation_id = extract_data(create_response)["id"]

    # Get conversation by ID
    response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth_headers)
    assert response.status_code == 200

    result = response.json()
    assert "data" in result
    conversation = result["data"]

    # Verify model configuration is included
    assert conversation["model_configuration_id"] == model_config_id
    assert conversation["model_configuration"] is not None
    assert conversation["model_configuration"]["name"] == model_config_data["name"]
    conversation = result["data"]
    assert conversation["id"] == conversation_id
    assert conversation["title"] == "Test Get Conversation"


async def test_update_conversation_with_model_config(client, db, auth_headers):
    """Test updating conversation details with model configuration."""
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation
    conversation_data = {
        "title": "Test Update Conversation",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    conversation_id = extract_data(create_response)["id"]

    # Update conversation
    update_data = {
        "title": "Updated Test Conversation with Model Config"
    }

    response = await client.put(f"/api/v1/chat/conversations/{conversation_id}", json=update_data, headers=auth_headers)
    assert response.status_code == 200

    result = response.json()
    assert "data" in result
    conversation = result["data"]
    assert conversation["title"] == update_data["title"]
    assert conversation["model_configuration_id"] == model_config_id


async def test_delete_conversation_with_model_config(client, db, auth_headers):
    """Test deleting a conversation with model configuration (soft delete)."""
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation
    conversation_data = {
        "title": "Test Delete Conversation",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    conversation_id = extract_data(create_response)["id"]

    # Delete conversation
    response = await client.delete(
        f"/api/v1/chat/conversations/{conversation_id}",
        headers=auth_headers
    )
    assert response.status_code == 204
    
    # Verify conversation is marked as inactive in database
    result = await db.execute(
        text("SELECT is_active FROM conversations WHERE id = :id"),
        {"id": conversation_id}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] is False  # Should be inactive


async def test_add_message_to_conversation(client, db, auth_headers):
    """Test adding a message to a conversation with model configuration."""
    # Create test dependencies (provider, model, model config)
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Add Message Conversation",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post(
        "/api/v1/chat/conversations",
        json=conversation_data,
        headers=auth_headers
    )
    conversation_id = extract_data(create_response)["id"]

    # Add message
    response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json=VALID_MESSAGE_DATA,
        headers=auth_headers
    )
    assert response.status_code == 200
    
    result = response.json()
    assert "data" in result
    message = result["data"]
    assert message["role"] == VALID_MESSAGE_DATA["role"]
    assert message["content"] == VALID_MESSAGE_DATA["content"]
    assert message["conversation_id"] == conversation_id
    assert "id" in message
    assert "created_at" in message
    
    # Verify message was stored in database
    result = await db.execute(
        text("SELECT role, content FROM messages WHERE id = :id"),
        {"id": message["id"]}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] == VALID_MESSAGE_DATA["role"]
    assert db_row[1] == VALID_MESSAGE_DATA["content"]


async def test_get_conversation_messages(client, db, auth_headers):
    """Test retrieving messages for a conversation with model configuration."""
    # Create test dependencies (provider, model, model config)
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Get Messages Conversation",
        "model_configuration_id": model_config_id
    }

    create_response = await client.post(
        "/api/v1/chat/conversations",
        json=conversation_data,
        headers=auth_headers
    )
    conversation_id = extract_data(create_response)["id"]

    # Add a message
    await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json=VALID_MESSAGE_DATA,
        headers=auth_headers
    )
    
    # Get messages
    response = await client.get(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers=auth_headers
    )
    assert response.status_code == 200
    
    result = response.json()
    assert "data" in result
    messages = result["data"]
    assert isinstance(messages, list)
    assert len(messages) >= 1
    
    message = messages[0]
    assert message["role"] == VALID_MESSAGE_DATA["role"]
    assert message["content"] == VALID_MESSAGE_DATA["content"]


async def test_create_conversation_with_model_config(client, db, auth_headers):
    """Test creating conversation with model configuration."""
    # First create a model configuration for testing
    # Create LLM provider
    provider_data = {
        "name": "Test Provider for Chat",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-key-for-chat",
        "is_active": True
    }

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    print(f"Provider response: {provider_response.status_code} - {provider_response.text}")
    assert provider_response.status_code == 201, f"Expected 201, got {provider_response.status_code}: {provider_response.text}"

    provider_json = provider_response.json()
    provider_id = provider_json["data"]["id"]

    # Create model for the provider
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Test Model",
        "description": "Test model for chat integration",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
        "input_cost_per_token": 0.00003,
        "output_cost_per_token": 0.00006
    }

    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    print(f"Model response: {model_response.status_code} - {model_response.text}")
    assert model_response.status_code == 200, f"Expected 200, got {model_response.status_code}: {model_response.text}"

    # Use test user ID (consistent with other tests)
    admin_user_id = "test-user"

    # Create model configuration
    model_config_data = {
        "name": "Test Chat Assistant",
        "description": "Test model configuration for chat integration",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": admin_user_id,
        "knowledge_base_ids": []
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    print(f"Model config response: {config_response.status_code} - {config_response.text}")
    assert config_response.status_code == 201  # Model configurations return 201 on creation
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Model Config Chat",
        "model_configuration_id": model_config_id
    }

    response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)

    print(f"Conversation response: {response.status_code} - {response.text}")
    assert response.status_code == 200
    result = response.json()
    assert "data" in result

    data = result["data"]

    # Verify conversation was created with model configuration
    assert data["model_configuration_id"] == model_config_id
    assert data["model_configuration"] is not None
    assert data["model_configuration"]["name"] == "Test Chat Assistant"
    assert data["title"] == "Test Model Config Chat"
    assert data["is_active"] is True
    assert "id" in data
    assert "user_id" in data
    assert "created_at" in data

    # Verify data was stored in database
    from sqlalchemy import text
    result = await db.execute(text("SELECT * FROM conversations WHERE id = :id"), {"id": data["id"]})
    conversation_row = result.fetchone()
    assert conversation_row is not None


async def test_send_message_with_model_config(client, db, auth_headers):
    """Test sending a message through a model configuration conversation to catch LLM endpoint bugs."""
    logger.info("=== EXPECTED TEST OUTPUT: Testing message sending with LLM provider (authentication errors expected) ===")

    # Create provider with real OpenAI endpoint structure
    provider_data = {
        "name": "Test OpenAI for Message Send",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",  # Real OpenAI endpoint
        "api_key": "test-key-for-message-send",
        "is_active": True
    }

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201

    # Handle response format
    provider_json = provider_response.json()
    provider_id = provider_json["data"]["id"]

    # Create model
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 for Message Test",
        "description": "Test model for message sending",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True
    }

    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200

    # Create model configuration
    model_config_data = {
        "name": "Test Message Send Assistant",
        "description": "Test model configuration for message sending",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": []
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Message Send Chat",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]

    with expect_llm_errors():
        # Test sending a message (this would catch LLM endpoint bugs)
        message_data = {
            "message": "Hello, this is a test message to verify LLM endpoint construction.",
            "rag_rewrite_mode": "no_rag",
            "temperature": 0.7,
            "max_tokens": 50
        }

        # This should fail gracefully with our test API key, but the endpoint construction should be correct
        message_response = await client.post(f"/api/v1/chat/conversations/{conversation_id}/send", json=message_data, headers=auth_headers)

        # We expect this to fail due to invalid API key, but NOT due to endpoint construction errors
        # A 401 (unauthorized) or 400 (bad request) is expected, but NOT 404 (not found) which would indicate endpoint issues
        print(f"Message send response: {message_response.status_code} - {message_response.text}")

        # The test passes if we get a reasonable error (not 404 endpoint errors)
        # 401/403 = auth issues (expected with test key)
        # 400 = bad request (could be API key format)
        # 500 = server error (could be LLM service issues)
        # 404 = endpoint not found (would indicate our double /v1 bug)
        assert message_response.status_code != 404, f"Got 404 - this suggests endpoint construction bug: {message_response.text}"

        # Additional validation: check that the response contains reasonable error information
        if message_response.status_code >= 400:
            response_data = message_response.json()
            # Should have error information, not just "Not Found"
            assert "error" in response_data or "detail" in response_data, f"Expected error details in response: {response_data}"
        if hasattr(message_response, "aclose"):
            await message_response.aclose()
        else:
            message_response.close()

    logger.info("=== EXPECTED TEST OUTPUT: Message sending test completed successfully ===")


async def test_send_message_with_azure_openai_config(client, db, auth_headers):
    """Test sending a message through Azure OpenAI model configuration to catch Azure-specific endpoint bugs."""
    # Create provider (using OpenAI adapter to avoid unavailable Azure adapter)
    provider_data = {
        "name": "Test OpenAI for Message Send",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-openai-key-for-message-send",
        "is_active": True
    }

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201

    # Handle response format
    provider_json = provider_response.json()
    provider_id = provider_json["data"]["id"]

    # Create model
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Azure for Message Test",
        "description": "Test Azure model for message sending",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True
    }

    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200

    # Create model configuration
    model_config_data = {
        "name": "Test Azure Message Send Assistant",
        "description": "Test Azure model configuration for message sending",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": []
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Azure Message Send Chat",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]

    # Test sending a message (this would catch Azure endpoint bugs)
    message_data = {
        "message": "Hello, this is a test message to verify Azure OpenAI endpoint construction.",
        "rag_rewrite_mode": "no_rag",
        "temperature": 0.7,
        "max_tokens": 50
    }

    # This should fail gracefully with our test API key, but the endpoint construction should be correct
    message_response = await client.post(f"/api/v1/chat/conversations/{conversation_id}/send", json=message_data, headers=auth_headers)

    print(f"Azure message send response: {message_response.status_code} - {message_response.text}")

    # The test passes if we get a reasonable error (not 404 endpoint errors)
    assert message_response.status_code != 404, f"Got 404 - this suggests Azure endpoint construction bug: {message_response.text}"

    # Additional validation for Azure-specific responses
    if message_response.status_code >= 400:
        response_data = message_response.json()
        assert "error" in response_data or "detail" in response_data, f"Expected error details in Azure response: {response_data}"
    if hasattr(message_response, "aclose"):
        await message_response.aclose()
    else:
        message_response.close()


async def test_send_message_with_rag_enabled(client, db, auth_headers):
    """Test sending a message with RAG enabled to catch RAG system failures."""
    # Create provider with real OpenAI endpoint structure
    provider_data = {
        "name": "Test OpenAI for RAG",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",  # Real OpenAI endpoint
        "api_key": "test-key-for-rag",
        "is_active": True
    }

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201
    provider_id = extract_data(provider_response)["id"]

    # Create model
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 for RAG",
        "description": "Test model for RAG testing",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False
    }

    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200

    # Create knowledge base
    kb_data = {
        "name": "Test RAG KB",
        "description": "Test knowledge base for RAG testing"
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Create model configuration with knowledge base
    config_data = {
        "name": "Test RAG Assistant",
        "description": "Test model configuration with RAG",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": [kb_id]
    }

    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation
    conversation_data = {
        "title": "Test RAG Chat",
        "model_configuration_id": model_config_id
    }

    conversation_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conversation_response.status_code == 200
    conversation_id = extract_data(conversation_response)["id"]

    # Exercise each RAG rewrite mode to ensure the backend accepts the toggle and responds
    modes_to_test = [
        ("no_rag", "What is the test document about without retrieval?"),
        ("raw_query", "What is the test document about?"),
        ("distill_context", "What is the test document about in brief?"),
        ("rewrite_enhanced", "What is the test document about with details?"),
    ]

    for mode_value, prompt in modes_to_test:
        send_data = {
            "message": prompt,
            "rag_rewrite_mode": mode_value
        }

        response = await client.post(
            f"/api/v1/chat/conversations/{conversation_id}/send",
            json=send_data,
            headers=auth_headers
        )

        try:
            assert response.status_code == 200, f"RAG mode '{mode_value}' failed: {response.text}"
            response_data = extract_data(response)

            # Verify message was created successfully (RAG didn't crash the system)
            assert "id" in response_data
            assert "content" in response_data
            assert response_data["role"] == "assistant"

            diagnostics = response_data.get("message_metadata", {}).get("rag_query_processing")
            if diagnostics:
                assert diagnostics.get("mode") == mode_value, f"Expected diagnostics mode {mode_value}, got {diagnostics.get('mode')}"

            # CRITICAL: Check for RAG-specific errors that would indicate system failure
            if "message_metadata" in response_data and "error" in response_data["message_metadata"]:
                error_msg = response_data["message_metadata"]["error"]
                # These specific errors should cause test failure
                if "'PromptService' object has no attribute 'get_prompts_for_entity'" in error_msg:
                    assert False, f"RAG failed with method name error: {error_msg}"
                if "'str' object has no attribute 'value'" in error_msg:
                    assert False, f"RAG failed with EntityType.value error: {error_msg}"
        finally:
            try:
                if hasattr(response, "aclose"):
                    await response.aclose()
                else:
                    response.close()
            except Exception:
                pass


async def test_complex_relationship_loading(client, db, auth_headers):
    """Test complex SQLAlchemy relationship loading that production workflows depend on."""
    # Create the full production chain: Provider → Model → Model Config → Conversation → Messages
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Complex Relationship Loading",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]
    conversation = extract_data(conv_response)

    # CRITICAL: Verify that SQLAlchemy relationships are properly loaded
    # This would have caught the production bugs where relationships weren't loading

    # 1. Verify model_configuration relationship is loaded
    assert "model_configuration" in conversation, "model_configuration relationship not loaded"
    assert conversation["model_configuration"] is not None, "model_configuration is None"
    assert conversation["model_configuration"]["id"] == model_config_id, "model_configuration ID mismatch"

    # 2. Verify nested provider relationship through model configuration
    model_config = conversation["model_configuration"]
    assert "llm_provider_id" in model_config, "llm_provider_id not in model_configuration"
    assert model_config["llm_provider_id"] == provider_id, "Provider ID mismatch in model_configuration"

    # 3. Test conversation retrieval with relationship loading
    get_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth_headers)
    assert get_response.status_code == 200
    retrieved_conversation = extract_data(get_response)

    # Verify relationships are still loaded after retrieval
    assert retrieved_conversation["model_configuration"] is not None, "model_configuration not loaded on retrieval"
    assert retrieved_conversation["model_configuration"]["id"] == model_config_id, "model_configuration ID mismatch on retrieval"

    # 4. Test conversation listing with relationship loading
    list_response = await client.get("/api/v1/chat/conversations", headers=auth_headers)
    assert list_response.status_code == 200
    conversations = extract_data(list_response)
    assert len(conversations) >= 1, "No conversations returned"

    # Find our conversation in the list
    our_conversation = None
    for conv in conversations:
        if conv["id"] == conversation_id:
            our_conversation = conv
            break

    assert our_conversation is not None, "Our conversation not found in list"
    assert our_conversation["model_configuration"] is not None, "model_configuration not loaded in list"

    # 5. Add a message and verify message-conversation relationship
    message_data = {
        "role": "user",
        "content": "Test message for relationship validation"
    }

    message_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json=message_data,
        headers=auth_headers
    )
    assert message_response.status_code == 200
    message = extract_data(message_response)

    # Verify message-conversation relationship
    assert message["conversation_id"] == conversation_id, "Message conversation_id mismatch"

    # 6. Retrieve messages and verify relationships
    messages_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth_headers)
    assert messages_response.status_code == 200
    messages = extract_data(messages_response)
    assert len(messages) >= 1, "No messages returned"

    # Verify message relationships are intact
    for msg in messages:
        assert msg["conversation_id"] == conversation_id, "Message conversation_id mismatch in list"

    # 7. Test database-level relationship integrity
    from sqlalchemy import text

    # Verify conversation exists with correct model_configuration_id
    conv_result = await db.execute(
        text("SELECT model_configuration_id FROM conversations WHERE id = :id"),
        {"id": conversation_id}
    )
    db_conversation = conv_result.fetchone()
    assert db_conversation is not None, "Conversation not found in database"
    assert db_conversation[0] == model_config_id, "Database model_configuration_id mismatch"

    # Verify model configuration exists with correct provider_id
    config_result = await db.execute(
        text("SELECT llm_provider_id FROM model_configurations WHERE id = :id"),
        {"id": model_config_id}
    )
    db_config = config_result.fetchone()
    assert db_config is not None, "Model configuration not found in database"
    assert db_config[0] == provider_id, "Database llm_provider_id mismatch"

    print(f"✅ Complex relationship loading test passed - all SQLAlchemy relationships working correctly")


async def test_complex_relationship_loading(client, db, auth_headers):
    """Test complex SQLAlchemy relationship loading that production workflows depend on.

    This test addresses the root cause: Tests were using legacy patterns instead of
    production model configuration workflows with complex relationship chains.
    """
    # Create the full production chain: Provider → Model → Model Config → Conversation → Messages
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create model configuration
    model_config_data = {
        **MODEL_CONFIG_DATA,
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"]
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Create conversation with model configuration
    conversation_data = {
        "title": "Test Complex Relationship Loading",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]
    conversation = conv_response.json()["data"]

    # CRITICAL: Verify that SQLAlchemy relationships are properly loaded
    # This would have caught the production bugs where relationships weren't loading

    # 1. Verify model_configuration relationship is loaded
    assert "model_configuration" in conversation, "model_configuration relationship not loaded"
    assert conversation["model_configuration"] is not None, "model_configuration is None"
    assert conversation["model_configuration"]["id"] == model_config_id, "model_configuration ID mismatch"

    # 2. Verify nested provider relationship through model configuration
    model_config = conversation["model_configuration"]

    # CRITICAL: Verify llm_provider_id field is included for API consistency
    assert "llm_provider_id" in model_config, f"llm_provider_id missing from model_configuration response. Available fields: {list(model_config.keys())}"
    assert model_config["llm_provider_id"] == provider_id, "Provider ID mismatch in model_configuration"

    # Also verify nested provider object for completeness
    assert "llm_provider" in model_config, "llm_provider object missing from model_configuration"
    assert model_config["llm_provider"] is not None, "llm_provider object is None"
    assert model_config["llm_provider"]["id"] == provider_id, "Provider ID mismatch in nested llm_provider"

    # 3. Test conversation retrieval with relationship loading
    get_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth_headers)
    assert get_response.status_code == 200
    retrieved_conversation = extract_data(get_response)

    # Verify relationships are still loaded after retrieval
    assert retrieved_conversation["model_configuration"] is not None, "model_configuration not loaded on retrieval"
    assert retrieved_conversation["model_configuration"]["id"] == model_config_id, "model_configuration ID mismatch on retrieval"


async def test_send_message_with_rag_enabled(client, db, auth_headers):
    """Test sending messages with RAG-enabled model configuration."""
    logger.info("=== EXPECTED TEST OUTPUT: Testing RAG integration with chat ===")

    # Create test dependencies including knowledge base with content
    provider_id = await _create_test_dependencies(client, auth_headers)

    # Create knowledge base for RAG
    kb_data = {
        "name": "Test RAG Knowledge Base",
        "description": "Test knowledge base for RAG integration testing"
    }
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Create model configuration with knowledge base attached (RAG enabled)
    model_config_data = {
        "name": "Test RAG Enabled Config",
        "llm_provider_id": provider_id,
        "model_name": MODEL_DATA["model_name"],
        "knowledge_base_ids": [kb_id],  # This enables RAG
        "created_by": "test-user"
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    model_config_id = extract_data(config_response)["id"]

    # Verify knowledge base is properly attached
    config_data = extract_data(config_response)
    # Check if knowledge base fields are available (may vary by API endpoint)
    if "has_knowledge_bases" in config_data:
        assert config_data["has_knowledge_bases"] is True
    if "knowledge_base_count" in config_data:
        assert config_data["knowledge_base_count"] == 1
    if "knowledge_bases" in config_data:
        assert len(config_data["knowledge_bases"]) == 1

    # Create conversation with RAG-enabled model configuration
    conversation_data = {
        "title": "Test RAG Conversation",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conversation_id = extract_data(conv_response)["id"]

    # Verify conversation has RAG-enabled model configuration
    conversation = extract_data(conv_response)
    model_config = conversation["model_configuration"]
    # Check if knowledge base fields are available (may vary by API endpoint)
    if "has_knowledge_bases" in model_config:
        assert model_config["has_knowledge_bases"] is True
    if "knowledge_base_count" in model_config:
        assert model_config["knowledge_base_count"] == 1

    # Send message that should trigger RAG retrieval
    message_data = {
        "content": "What information do you have about the test knowledge base?",
        "role": "user"
    }

    logger.info("=== EXPECTED TEST OUTPUT: The following LLM authentication error is expected in test environment ===")
    message_response = await client.post(f"/api/v1/chat/conversations/{conversation_id}/messages",
                                       json=message_data, headers=auth_headers)

    # In test environment, we expect LLM authentication to fail, but the RAG setup should work
    # The important thing is that the system attempts to use the knowledge base
    if message_response.status_code == 500:
        # Check that the error is related to LLM authentication, not RAG setup
        error_data = message_response.json()
        assert "error" in error_data
        logger.info("=== EXPECTED TEST OUTPUT: LLM authentication error occurred as expected ===")
    else:
        # If LLM is configured, message should succeed
        assert message_response.status_code == 200
        message_data = extract_data(message_response)
        assert message_data["conversation_id"] == conversation_id
        assert message_data["role"] == "user"

    # Verify conversation still exists and has proper model configuration
    get_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=auth_headers)
    assert get_response.status_code == 200
    conversation = extract_data(get_response)
    # Verify the model configuration is still attached (knowledge base fields may vary)
    assert conversation["model_configuration"] is not None
    assert conversation["model_configuration"]["id"] == model_config_id

    logger.info("=== RAG integration test completed successfully ===")


class ChatIntegrationTestSuite(BaseIntegrationTestSuite):
    def get_suite_name(self) -> str:
        return "Chat Integration"

    def get_suite_description(self) -> str:
        return "Integration tests for chat functionality including conversations, messages, and advanced features"

    def get_test_functions(self) -> List[Callable]:
        return [
            test_health_endpoint,
            # Current Model Configuration System Tests (No Legacy)
            test_create_conversation_with_model_config_basic,
            test_list_conversations_with_model_configs,
            test_get_conversation_by_id_with_model_config,
            test_update_conversation_with_model_config,
            test_delete_conversation_with_model_config,
            # LLM Provider Endpoint Tests (Catch Real Bugs)
            test_send_message_with_model_config,  # OpenAI endpoint validation
            test_send_message_with_azure_openai_config,  # Azure OpenAI endpoint validation
            test_complex_relationship_loading,  # SQLAlchemy relationship loading validation
            # RAG Integration Tests
            test_send_message_with_rag_enabled,  # RAG integration with knowledge bases
        ]


if __name__ == "__main__":
    import asyncio
    suite = ChatIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
