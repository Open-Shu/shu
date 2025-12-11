"""
Integration tests for RAG (Retrieval-Augmented Generation) functionality.

Tests the enhanced RAG system including:
- Enhanced context formatting
- Source citation tracking
- Cross-session memory
- Performance optimization
"""

import sys
import os
from typing import Dict, Any

from integ.helpers.api_helpers import process_streaming_result
from integ.response_utils import extract_data
from integ.base_integration_test import BaseIntegrationTestSuite


# Test Data
PROVIDER_DATA = {
    "name": "Test RAG Provider",
    "provider_type": "local",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-12345",
    "is_active": True
}

MODEL_DATA = {
    "model_name": "gpt-4",
    "display_name": "GPT-4 RAG Test Model",
    "description": "Test model for RAG integration testing",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False
}

MODEL_CONFIG_DATA = {
    "name": "Test RAG Assistant",
    "description": "Test model configuration for RAG testing",
    "model_name": "gpt-4",  # This will be set dynamically from model_data
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": []
}

KB_DATA = {
    "name": "Test RAG KB",
    "description": "Test knowledge base for RAG testing"
}


async def test_enhanced_rag_context_formatting(client, db, auth_headers):
    """Test enhanced RAG context formatting with citations."""
    # Create test LLM provider
    provider_response = await client.post(
        "/api/v1/llm/providers",
        json=PROVIDER_DATA,
        headers=auth_headers
    )
    assert provider_response.status_code == 201, provider_response.text
    provider_data = provider_response.json()["data"]

    # Create test model
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_data['id']}/models",
        json=MODEL_DATA,
        headers=auth_headers
    )
    assert model_response.status_code == 200, model_response.text  # Model creation returns 200, not 201
    model_data = model_response.json()["data"]

    # Create knowledge base first (needed for model configuration)
    kb_response = await client.post(
        "/api/v1/knowledge-bases",
        json=KB_DATA,
        headers=auth_headers
    )
    assert kb_response.status_code == 201, kb_response.text
    kb_data = kb_response.json()["data"]

    # Create model configuration with knowledge base attached
    config_data = MODEL_CONFIG_DATA.copy()
    config_data["llm_provider_id"] = provider_data["id"]
    config_data["model_name"] = model_data["model_name"]
    config_data["knowledge_base_ids"] = [kb_data["id"]]  # Attach KB during creation
    config_response = await client.post(
        "/api/v1/model-configurations",
        json=config_data,
        headers=auth_headers
    )
    assert config_response.status_code == 201, config_response.tetx
    model_config_data = config_response.json()["data"]

    # Verify knowledge base is attached
    assert model_config_data["has_knowledge_bases"] is True, model_config_data
    assert model_config_data["knowledge_base_count"] == 1, model_config_data
    assert len(model_config_data["knowledge_bases"]) == 1, model_config_data
    assert model_config_data["knowledge_bases"][0]["id"] == kb_data["id"], model_config_data

    # Create conversation
    conversation_response = await client.post(
        "/api/v1/chat/conversations",
        json={
            "title": "Test RAG Conversation",
            "model_configuration_id": model_config_data["id"]
        },
        headers=auth_headers
    )
    assert conversation_response.status_code == 200, conversation_response.text
    conversation_data = conversation_response.json()["data"]

    # Send message that should trigger RAG
    message_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_data['id']}/send",
        json={
            "message": "What is the test document about?",
            "rag_rewrite_mode": "raw_query",
        },
        headers=auth_headers
    )
    assert message_response.status_code == 200, message_response.text
    message_response = await process_streaming_result(message_response)
    assert message_response["content"] is not None
    assert message_response["content"] == "Echo: What is the test document about?", message_response


async def test_rag_performance_caching(client, db, auth_headers):
    """Test RAG performance optimization with caching."""
    # Create test LLM provider
    provider_response = await client.post(
        "/api/v1/llm/providers",
        json=PROVIDER_DATA,
        headers=auth_headers
    )
    assert provider_response.status_code == 201
    provider_data = provider_response.json()["data"]

    # Create test model
    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_data['id']}/models",
        json=MODEL_DATA,
        headers=auth_headers
    )
    assert model_response.status_code == 200  # Model creation returns 200, not 201
    model_data = model_response.json()["data"]

    # Create model configuration
    config_data = MODEL_CONFIG_DATA.copy()
    config_data["llm_provider_id"] = provider_data["id"]
    config_data["model_name"] = model_data["model_name"]
    config_response = await client.post(
        "/api/v1/model-configurations",
        json=config_data,
        headers=auth_headers
    )
    assert config_response.status_code == 201
    model_config_data = config_response.json()["data"]

    # Create conversation
    conversation_response = await client.post(
        "/api/v1/chat/conversations",
        json={
            "title": "Test RAG Cache Conversation",
            "model_configuration_id": model_config_data["id"]
        },
        headers=auth_headers
    )
    assert conversation_response.status_code == 200
    conversation_data = conversation_response.json()["data"]

    # Send first message (should cache results)
    first_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_data['id']}/send",
        json={
            "message": "What is the test document about?",
            "rag_rewrite_mode": "raw_query",
        },
        headers=auth_headers
    )

    assert first_response.status_code == 200

    # Send same message again (should use cache)
    second_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_data['id']}/send",
        json={
            "message": "What is the test document about?",
            "rag_rewrite_mode": "raw_query",
        },
        headers=auth_headers
    )

    assert second_response.status_code == 200
    second_response = await process_streaming_result(second_response)
    assert second_response["content"] is not None
    assert second_response["content"] == "Echo: What is the test document about?", second_response


class RAGIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for RAG functionality."""

    def get_test_functions(self):
        """Return list of test functions for this suite."""
        return [
            test_enhanced_rag_context_formatting,
            test_rag_performance_caching,
        ]

    def get_suite_name(self):
        """Return the name of this test suite."""
        return "🚀 Shu RAG Integration"

    def get_suite_description(self):
        """Return description of this test suite for CLI help."""
        return "Integration tests for RAG (Retrieval-Augmented Generation) functionality including enhanced context formatting, source citation tracking, and performance optimization."


if __name__ == "__main__":
    import asyncio
    suite = RAGIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
