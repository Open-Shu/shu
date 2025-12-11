"""
Error Recovery and Resilience Integration Tests

These tests verify system robustness under failure conditions including:
- LLM provider failures and timeouts
- Database connection issues and recovery
- Invalid API keys and authentication failures
- Network connectivity problems
- Graceful degradation scenarios
- Error handling and user feedback
"""

import sys
import os
from typing import List, Callable
import asyncio
import uuid

from integ.helpers.api_helpers import process_streaming_result
from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_llm_errors,
    ExpectedErrorContext
)


# Test Data for Error Recovery - Use functions to generate unique data
def get_invalid_provider_data():
    unique_id = str(uuid.uuid4())[:8]
    return {
        "name": f"test_invalid_provider_{unique_id}",
        "provider_type": "openai",
        "api_endpoint": "https://invalid-endpoint.example.com/v1",
        "api_key": f"invalid-key-{unique_id}",
        "is_active": True
    }

def get_timeout_provider_data():
    unique_id = str(uuid.uuid4())[:8]
    return {
        "name": f"test_timeout_provider_{unique_id}",
        "provider_type": "openai",
        "api_endpoint": "https://httpstat.us/200?sleep=30000",  # 30 second delay
        "api_key": f"test-timeout-key-{unique_id}",
        "is_active": True
    }

def get_malformed_provider_data():
    unique_id = str(uuid.uuid4())[:8]
    return {
        "name": f"test_malformed_provider_{unique_id}",
        "provider_type": "openai",
        "api_endpoint": "not-a-valid-url",
        "api_key": f"test-malformed-key-{unique_id}",
        "is_active": True
    }


async def test_llm_provider_invalid_api_key(client, db, auth_headers):
    """Test system behavior with invalid LLM provider API key."""
    # Create provider with invalid API key
    invalid_provider_data = get_invalid_provider_data()
    provider_response = await client.post("/api/v1/llm/providers", json=invalid_provider_data, headers=auth_headers)
    assert provider_response.status_code == 201
    provider_response_data = provider_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in provider_response_data:
        provider_id = provider_response_data["data"]["id"]
    else:
        provider_id = provider_response_data["id"]
    
    # Create model
    unique_id = str(uuid.uuid4())[:8]
    model_data = {
        "model_name": "gpt-4",
        "display_name": f"test_gpt4_invalid_key_{unique_id}",
        "description": f"Test model with invalid API key {unique_id}",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False
    }
    
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200
    
    # Create model configuration
    model_config_data = {
        "name": f"test_invalid_key_assistant_{unique_id}",
        "description": f"Test model configuration with invalid API key {unique_id}",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": []
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    config_response_data = config_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in config_response_data:
        model_config_id = config_response_data["data"]["id"]
    else:
        model_config_id = config_response_data["id"]
    
    # Create conversation
    conversation_data = {
        "title": f"test_invalid_key_conversation_{unique_id}",
        "model_configuration_id": model_config_id
    }
    
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conv_response_data = conv_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in conv_response_data:
        conversation_id = conv_response_data["data"]["id"]
    else:
        conversation_id = conv_response_data["id"]
    
    # Send message (should fail gracefully with invalid API key)
    message_data = {
        "message": "Test message with invalid API key",
        "rag_rewrite_mode": "no_rag",
    }
    
    message_response = await client.post(f"/api/v1/chat/conversations/{conversation_id}/send", json=message_data, headers=auth_headers)
    message_response = await process_streaming_result(message_response)
    assert message_response.startswith("LLM provider error:")
    
    # Verify system is still responsive
    health_response = await client.get("/api/v1/health", headers=auth_headers)
    assert health_response.status_code == 200, "System should remain responsive after LLM failure"
    
    print(f"✅ LLM provider invalid API key handled gracefully")
    return True


async def test_llm_provider_invalid_endpoint(client, db, auth_headers):
    """Test system behavior with invalid LLM provider endpoint."""
    # Create provider with invalid endpoint
    malformed_provider_data = get_malformed_provider_data()
    provider_response = await client.post("/api/v1/llm/providers", json=malformed_provider_data, headers=auth_headers)
    assert provider_response.status_code == 201
    provider_response_data = provider_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in provider_response_data:
        provider_id = provider_response_data["data"]["id"]
    else:
        provider_id = provider_response_data["id"]
    
    # Create model
    model_data = {
        "model_name": "gpt-4",
        "display_name": "GPT-4 Invalid Endpoint Test",
        "description": "Test model with invalid endpoint",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False
    }
    
    model_response = await client.post(f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers)
    assert model_response.status_code == 200
    
    # Create model configuration
    model_config_data = {
        "name": "Invalid Endpoint Test Assistant",
        "description": "Test model configuration with invalid endpoint",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": []
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201
    config_response_data = config_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in config_response_data:
        model_config_id = config_response_data["data"]["id"]
    else:
        model_config_id = config_response_data["id"]

    # Create conversation
    conversation_data = {
        "title": "Invalid Endpoint Test Conversation",
        "model_configuration_id": model_config_id
    }

    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code == 200
    conv_response_data = conv_response.json()
    # Handle both response formats: {"data": ...} and direct response
    if "data" in conv_response_data:
        conversation_id = conv_response_data["data"]["id"]
    else:
        conversation_id = conv_response_data["id"]
    
    # Send message (should fail gracefully with invalid endpoint)
    message_data = {
        "message": "Test message with invalid endpoint",
        "rag_rewrite_mode": "no_rag",
    }
    
    message_response = await client.post(f"/api/v1/chat/conversations/{conversation_id}/send", json=message_data, headers=auth_headers)
    message_response = await process_streaming_result(message_response)
    assert message_response.startswith("LLM provider error:")
    
    # Verify system is still responsive
    health_response = await client.get("/api/v1/health", headers=auth_headers)
    assert health_response.status_code == 200, "System should remain responsive after endpoint failure"
    
    print(f"✅ LLM provider invalid endpoint handled gracefully")
    return True


async def test_database_resilience(client, db, auth_headers):
    """Test system behavior under database stress and connection issues."""
    # Test creating many entities rapidly to stress database connections
    kb_ids = []
    
    try:
        # Create multiple knowledge bases rapidly
        for i in range(5):
            unique_id = str(uuid.uuid4())[:8]
            kb_data = {
                "name": f"test_db_stress_kb_{i}_{unique_id}",
                "description": f"Test knowledge base for database stress testing {i} {unique_id}"
            }
            
            kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
            if kb_response.status_code == 201:
                kb_response_data = kb_response.json()
                # Handle both response formats: {"data": ...} and direct response
                if "data" in kb_response_data:
                    kb_ids.append(kb_response_data["data"]["id"])
                else:
                    kb_ids.append(kb_response_data["id"])
            else:
                print(f"⚠️  KB creation failed at iteration {i}: {kb_response.status_code}")
        
        print(f"✅ Created {len(kb_ids)} knowledge bases under stress")
        
        # Test concurrent operations
        tasks = []
        for kb_id in kb_ids[:3]:  # Test first 3 KBs
            task = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
            tasks.append(task)
        
        # Execute concurrent requests
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_responses = 0
        for i, response in enumerate(responses):
            if hasattr(response, 'status_code') and response.status_code == 200:
                successful_responses += 1
            else:
                print(f"⚠️  Concurrent request {i} failed: {response}")
        
        print(f"✅ {successful_responses}/{len(responses)} concurrent requests succeeded")
        
        # Verify system is still responsive
        health_response = await client.get("/api/v1/health", headers=auth_headers)
        assert health_response.status_code == 200, "System should remain responsive after database stress"
        
    except Exception as e:
        print(f"⚠️  Database stress test encountered exception: {e}")
        # System should still be responsive even if stress test fails
        health_response = await client.get("/api/v1/health", headers=auth_headers)
        assert health_response.status_code == 200, "System should remain responsive even after database exceptions"
    
    print(f"✅ Database resilience test completed")
    return True


async def test_authentication_failure_recovery(client, db, auth_headers):
    """Test system behavior with authentication failures."""
    # Test with invalid auth headers
    invalid_headers = {"Authorization": "Bearer invalid-token-12345"}
    
    # Test various endpoints with invalid auth
    endpoints_to_test = [
        "/api/v1/knowledge-bases",
        "/api/v1/llm/providers", 
        "/api/v1/model-configurations",
        "/api/v1/chat/conversations"
    ]
    
    auth_failures = 0
    for endpoint in endpoints_to_test:
        response = await client.get(endpoint, headers=invalid_headers)
        if response.status_code == 401:
            auth_failures += 1
        else:
            print(f"⚠️  Endpoint {endpoint} returned {response.status_code} instead of 401 for invalid auth")
    
    print(f"✅ {auth_failures}/{len(endpoints_to_test)} endpoints properly rejected invalid auth")
    
    # Test with no auth headers
    no_auth_failures = 0
    for endpoint in endpoints_to_test:
        response = await client.get(endpoint)  # No headers
        if response.status_code in [401, 403]:
            no_auth_failures += 1
        else:
            print(f"⚠️  Endpoint {endpoint} returned {response.status_code} instead of 401/403 for no auth")
    
    print(f"✅ {no_auth_failures}/{len(endpoints_to_test)} endpoints properly rejected missing auth")
    
    # Verify system is still responsive with valid auth
    health_response = await client.get("/api/v1/health", headers=auth_headers)
    assert health_response.status_code == 200, "System should remain responsive after auth failures"
    
    valid_response = await client.get("/api/v1/knowledge-bases", headers=auth_headers)
    assert valid_response.status_code == 200, "Valid auth should still work after auth failures"
    
    print(f"✅ Authentication failure recovery test completed")
    return True


class ErrorRecoveryIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Error Recovery and Resilience functionality."""

    def get_test_functions(self) -> List[Callable]:
        """Return error recovery test functions."""
        return [
            test_llm_provider_invalid_api_key,
            test_llm_provider_invalid_endpoint,
            test_database_resilience,
            test_authentication_failure_recovery,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Error Recovery and Resilience Integration"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Integration tests for system robustness including LLM failures, database issues, auth failures, and graceful degradation"


if __name__ == "__main__":
    import asyncio
    suite = ErrorRecoveryIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
