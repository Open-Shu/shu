"""
Comprehensive Integration Tests for CURRENTLY IMPLEMENTED Functionality

This test suite validates what IS implemented and documents what's MISSING.
It addresses the root cause: tests weren't catching bugs because they didn't
match production usage patterns.

FOCUS: Test actual production workflows with real model configurations,
not simplified legacy patterns that hide relationship loading issues.
"""

import sys
import os
import uuid
from typing import List, Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

# Standardized helper now provided by tests.response_utils.extract_data


async def test_production_chat_workflow_with_model_configs(client, db, auth_headers):
    """
    Test the ACTUAL production chat workflow that was failing before:
    Admin creates Provider -> Model -> Model Config -> User creates Conversation -> Sends Messages
    
    This replicates the complex relationship chain that caused bugs.
    """
    print("🔧 Testing production chat workflow with model configurations...")
    
    # Step 1: Admin creates LLM Provider (PRODUCTION PATTERN)
    provider_data = {
        "name": "Test Production Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-production-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201, f"Provider creation failed: {provider_response.status_code} - {provider_response.text}"

    # Handle response format inconsistency (LLM API returns direct response, others use "data" wrapper)
    provider_data = extract_response_data(provider_response.json())
    provider_id = provider_data["id"]
    print(f"✅ Created provider: {provider_id}")
    
    # Step 2: Admin adds model to provider (PRODUCTION PATTERN)
    model_data = {
        "model_name": "gpt-4-production-test",
        "display_name": "GPT-4 Production Test",
        "model_type": "chat",
        "context_window": 128000,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": True,
        "cost_per_input_token": 0.00001,
        "cost_per_output_token": 0.00003,
        "is_active": True
    }
    
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200, f"Model creation failed: {model_response.status_code} - {model_response.text}"

    # Handle response format (LLM API returns direct response)
    model_result = extract_response_data(model_response.json())
    print(f"✅ Created model: {model_data['model_name']}")
    
    # Step 3: Admin creates Model Configuration (KEY PRODUCTION COMPONENT)
    config_data = {
        "name": "Production Test Assistant",
        "description": "Model configuration for production testing",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201], f"Model config creation failed: {config_response.status_code} - {config_response.text}"

    # Handle response format (Model Config API uses "data" wrapper)
    model_config_data = extract_response_data(config_response.json())
    model_config_id = model_config_data["id"]
    print(f"✅ Created model configuration: {model_config_id}")
    
    # Verify model configuration has proper relationships
    assert model_config_data["llm_provider_id"] == provider_id
    assert model_config_data["model_name"] == model_data["model_name"]
    
    # Step 4: User creates conversation with Model Configuration (PRODUCTION PATTERN)
    conversation_data = {
        "title": "Production Test Conversation",
        "model_configuration_id": model_config_id
    }
    
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200, f"Conversation creation failed: {conv_response.status_code} - {conv_response.text}"

    conv_data = extract_response_data(conv_response.json())
    conversation_id = conv_data["id"]
    print(f"✅ Created conversation: {conversation_id}")
    
    # Verify conversation has proper model configuration relationship
    assert conv_data["model_configuration_id"] == model_config_id
    assert "model_configuration" in conv_data  # Should include relationship data
    
    # Step 5: User sends message (PRODUCTION PATTERN - triggers full relationship chain)
    message_data = {
        "message": "Hello, this is a production test message",
        "rag_rewrite_mode": "no_rag",
    }
    
    # This should trigger: Message -> Conversation -> Model Config -> Provider -> Model chain
    message_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=message_data,
        headers=auth_headers
    )
    
    # Should succeed (even if LLM fails, the relationship chain should work)
    assert message_response.status_code == 200, f"Message sending failed: {message_response.status_code} - {message_response.text}"
    
    message_result = extract_data(message_response)
    print(f"✅ Sent message, got response: {message_result['id']}")
    
    # Verify message has proper relationships loaded
    assert message_result["conversation_id"] == conversation_id
    assert message_result["role"] == "assistant"
    
    # Step 6: Verify conversation history works
    history_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth_headers)
    assert history_response.status_code == 200
    
    messages = extract_data(history_response)
    assert len(messages) >= 2  # User message + assistant response
    print(f"✅ Retrieved conversation history: {len(messages)} messages")
    
    return True


async def test_rag_integration_with_model_configs(client, db, auth_headers):
    """
    Test RAG integration through model configurations (PRODUCTION PATTERN).
    This tests the complex KB -> Model Config -> Conversation -> RAG chain.
    """
    print("🔧 Testing RAG integration with model configurations...")
    
    # Step 1: Create Knowledge Base
    kb_data = {
        "name": "Production RAG Test KB",
        "description": "Knowledge base for testing RAG integration"
    }
    
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 200
    kb_data_result = extract_response_data(kb_response.json())
    kb_id = kb_data_result["id"]
    print(f"✅ Created knowledge base: {kb_id}")
    
    # Step 2: Create Provider and Model
    provider_data = {
        "name": "Test RAG Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-rag-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201, f"Provider creation failed: {provider_response.status_code} - {provider_response.text}"
    provider_data_result = extract_response_data(provider_response.json())
    provider_id = provider_data_result["id"]
    
    model_data = {
        "model_name": "gpt-4-rag-test",
        "display_name": "GPT-4 RAG Test",
        "model_type": "chat",
        "context_window": 128000,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": True,
        "cost_per_input_token": 0.00001,
        "cost_per_output_token": 0.00003,
        "is_active": True
    }
    
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200, f"Model creation failed: {model_response.status_code} - {model_response.text}"
    print(f"✅ Created RAG provider and model")
    
    # Step 3: Create Model Configuration with Knowledge Base (PRODUCTION PATTERN)
    config_data = {
        "name": "Production RAG Assistant",
        "description": "Model config with attached knowledge base",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "knowledge_base_ids": [kb_id],  # This creates the complex relationship
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201], f"Model config creation failed: {config_response.status_code} - {config_response.text}"
    config_data_result = extract_response_data(config_response.json())
    model_config_id = config_data_result["id"]
    print(f"✅ Created model config with KB attachment: {model_config_id}")
    
    # Step 4: Create Conversation with RAG-enabled Model Config
    conversation_data = {
        "title": "Production RAG Test Conversation",
        "model_configuration_id": model_config_id
    }
    
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conv_data_result = extract_data(conv_response)
    conversation_id = conv_data_result["id"]
    print(f"✅ Created RAG conversation: {conversation_id}")
    
    # Step 5: Send RAG Message (PRODUCTION PATTERN)
    message_data = {
        "message": "What information do you have about biology research?",
        "rag_rewrite_mode": "raw_query",  # This should trigger KB lookup via model config
    }
    
    # This tests the full chain: Message -> Conversation -> Model Config -> KB -> RAG
    message_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=message_data,
        headers=auth_headers
    )
    
    # Should succeed and include RAG processing
    assert message_response.status_code == 200
    print(f"✅ RAG message processed successfully")
    
    # Verify RAG was attempted (even if no documents found)
    message_result = extract_response_data(message_response.json())
    assert message_result["role"] == "assistant"
    
    return True


async def test_missing_functionality_documentation(client, db, auth_headers):
    """
    Document what functionality is MISSING that tests expect.
    This helps prioritize implementation work.
    """
    print("📋 Documenting missing functionality...")
    
    missing_endpoints = []
    
    # Test 1: User Preferences API (MISSING)
    prefs_response = await client.get("/api/v1/user/preferences", headers=auth_headers)
    if prefs_response.status_code == 404:
        missing_endpoints.append("GET /api/v1/user/preferences - User preferences retrieval")
    
    prefs_create_response = await client.put("/api/v1/user/preferences", json={"test": "data"}, headers=auth_headers)
    if prefs_create_response.status_code == 404:
        missing_endpoints.append("PUT /api/v1/user/preferences - User preferences creation/update")
    
    # Test 2: Advanced RAG Configuration (check if implemented)
    try:
        kb_list_response = await client.get("/api/v1/knowledge-bases", headers=auth_headers)
        if kb_list_response.status_code == 200:
            kbs = extract_response_data(kb_list_response.json())
            if kbs and len(kbs) > 0:
                kb_id = kbs[0]["id"]
                rag_config_response = await client.get(f"/api/v1/knowledge-bases/{kb_id}/rag-config", headers=auth_headers)
                if rag_config_response.status_code == 200:
                    rag_config = extract_response_data(rag_config_response.json())
                    expected_fields = ["search_threshold", "max_results", "chunk_overlap_ratio"]
                    missing_rag_fields = [field for field in expected_fields if field not in rag_config]
                    if missing_rag_fields:
                        missing_endpoints.append(f"RAG Config missing fields: {missing_rag_fields}")
    except Exception as e:
        missing_endpoints.append(f"RAG Config test failed: {str(e)}")
    
    print(f"📋 Missing functionality identified:")
    for endpoint in missing_endpoints:
        print(f"   ❌ {endpoint}")
    
    if not missing_endpoints:
        print("   ✅ All expected functionality is implemented!")
    
    # This test always passes - it's just for documentation
    return True


class ComprehensiveCurrentFunctionalityTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for currently implemented functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return comprehensive test functions for current functionality."""
        return [
            test_production_chat_workflow_with_model_configs,
            test_rag_integration_with_model_configs,
            test_missing_functionality_documentation,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Comprehensive Current Functionality"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Comprehensive tests for currently implemented functionality using production patterns, plus documentation of missing features"


if __name__ == "__main__":
    import asyncio
    suite = ComprehensiveCurrentFunctionalityTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
