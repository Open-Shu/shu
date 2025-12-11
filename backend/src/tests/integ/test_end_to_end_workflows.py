"""
End-to-End Workflow Integration Tests

These tests cover complete user workflows from start to finish:
- Admin sets up providers, models, configurations
- User creates conversations and sends messages
- RAG processing with knowledge bases
- Error recovery and resilience scenarios
- Performance under realistic load

These tests address the gap where individual components work but
the complete workflow has integration issues.
"""

import sys
import os
import asyncio
import time
from typing import List, Callable
from integ.response_utils import extract_data

from integ.base_integration_test import BaseIntegrationTestSuite


async def test_complete_admin_setup_workflow(client, db, auth_headers):
    """
    Test complete admin workflow: Provider -> Model -> Prompt -> Model Config -> KB -> Assignment
    This is the full setup an admin would do before users can chat.
    """
    # Step 1: Admin creates LLM Provider
    provider_data = {
        "name": "Complete Workflow Test Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-complete-workflow-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code in [200, 201], f"Provider creation failed: {provider_response.status_code} - {getattr(provider_response, 'text', '')}"
    provider_id = extract_data(provider_response)["id"]

    # Step 2: Admin adds models to provider
    model_data = {
        "model_name": "gpt-4-complete-workflow",
        "display_name": "GPT-4 Complete Workflow",
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
    assert model_response.status_code in [200, 201], f"Model creation failed: {model_response.status_code} - {getattr(model_response, 'text', '')}"

    # Step 3: Admin creates knowledge base
    kb_data = {
        "name": "Complete Workflow Test Knowledge Base",
        "description": "KB for complete workflow testing"
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code in [200, 201], f"KB creation failed: {kb_response.status_code} - {getattr(kb_response, 'text', '')}"
    kb_id = extract_data(kb_response)["id"]

    # Step 4: Admin creates prompt
    prompt_data = {
        "name": "Complete Workflow Test Prompt",
        "content": "You are a helpful assistant for complete workflow testing. Use provided context to answer questions accurately and cite sources.",
        "entity_type": "model_configuration",
        "is_active": True
    }
    
    prompt_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert prompt_response.status_code in [200, 201], f"Prompt creation failed: {prompt_response.status_code} - {getattr(prompt_response, 'text', '')}"
    prompt_id = extract_data(prompt_response)["id"]

    # Step 5: Admin creates model configuration with all components
    config_data = {
        "name": "Complete Workflow Assistant",
        "description": "Full model configuration for complete workflow testing",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "prompt_id": prompt_id,
        "knowledge_base_ids": [kb_id],
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201], f"Model configuration creation failed: {config_response.status_code} - {getattr(config_response, 'text', '')}"
    model_config_id = extract_data(config_response)["id"]

    # Step 6: Verify complete configuration is accessible
    get_config_response = await client.get(f"/api/v1/model-configurations/{model_config_id}", headers=auth_headers)
    assert get_config_response.status_code in [200, 201], f"Get model configuration failed: {getattr(get_config_response, 'status_code', 'n/a')} - {getattr(get_config_response, 'text', '')}"

    config_data = extract_data(get_config_response)
    assert config_data["name"] == "Complete Workflow Assistant"
    assert config_data["llm_provider_id"] == provider_id
    if "prompt_id" in config_data:
        assert config_data["prompt_id"] == prompt_id
    if "knowledge_base_ids" in config_data:
        assert kb_id in config_data.get("knowledge_base_ids", [])

    # Step 7: Verify configuration appears in listings
    list_response = await client.get("/api/v1/model-configurations", headers=auth_headers)
    assert list_response.status_code == 200

    configs_data = extract_data(list_response)
    configs = configs_data.get("items", configs_data) if isinstance(configs_data, dict) else configs_data
    config_names = [config["name"] for config in configs]
    assert "Complete Workflow Assistant" in config_names

    return True


async def test_complete_user_chat_workflow(client, db, auth_headers):
    """
    Test complete user workflow: Select Model Config -> Create Conversation -> Chat with RAG
    This is what a typical user would do when using the system.
    """
    # Setup: Create a model configuration (simulating admin setup)
    provider_data = {
        "name": "User Workflow Test Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-user-workflow-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code in [200, 201], f"Provider creation failed: {provider_response.status_code} - {getattr(provider_response, 'text', '')}"
    provider_id = extract_data(provider_response)["id"]

    model_data = {
        "model_name": "gpt-4-user-workflow",
        "display_name": "GPT-4 User Workflow",
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
    assert model_response.status_code in [200, 201], f"Model creation failed: {model_response.status_code} - {getattr(model_response, 'text', '')}"

    config_data = {
        "name": "User Workflow Assistant",
        "description": "Model config for user workflow testing",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201], f"Model configuration creation failed: {config_response.status_code} - {getattr(config_response, 'text', '')}"
    model_config_id = extract_data(config_response)["id"]

    # User Workflow Step 1: User lists available model configurations
    list_configs_response = await client.get("/api/v1/model-configurations", headers=auth_headers)
    assert list_configs_response.status_code == 200

    configs_data = extract_data(list_configs_response)
    configs = configs_data.get("items", configs_data) if isinstance(configs_data, dict) else configs_data
    available_config = None
    for config in configs:
        if config["name"] == "User Workflow Assistant":
            available_config = config
            break

    assert available_config is not None, "User should see available model configuration"
    assert available_config["is_active"] == True

    # User Workflow Step 2: User creates conversation with selected model config
    conversation_data = {
        "title": "My Research Chat",
        "model_configuration_id": model_config_id
    }
    
    conv_response = await client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
    assert conv_response.status_code in [200, 201], f"Conversation creation failed: {conv_response.status_code} - {getattr(conv_response, 'text', '')}"
    conversation_id = extract_data(conv_response)["id"]

    # User Workflow Step 3: User sends first message
    message1_data = {
        "message": "Hello! I'm starting a new research project on machine learning.",
        "rag_rewrite_mode": "no_rag",
    }
    
    msg1_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=message1_data,
        headers=auth_headers
    )
    try:
        assert msg1_response.status_code in [200, 201], f"First message send failed: {msg1_response.status_code} - {getattr(msg1_response, 'text', '')}"
    finally:
        try:
            if hasattr(msg1_response, "aclose"):
                await msg1_response.aclose()
            else:
                msg1_response.close()
        except Exception:
            pass

    # User Workflow Step 4: User sends follow-up message
    message2_data = {
        "message": "Can you help me understand neural networks?",
        "rag_rewrite_mode": "raw_query",  # User enables RAG for this question
    }
    
    msg2_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=message2_data,
        headers=auth_headers
    )
    try:
        assert msg2_response.status_code in [200, 201], f"Second message send failed: {msg2_response.status_code} - {getattr(msg2_response, 'text', '')}"
    finally:
        try:
            if hasattr(msg2_response, "aclose"):
                await msg2_response.aclose()
            else:
                msg2_response.close()
        except Exception:
            pass

    # User Workflow Step 5: User views conversation history
    history_response = await client.get(f"/api/v1/chat/conversations/{conversation_id}/messages", headers=auth_headers)
    assert history_response.status_code == 200
    
    messages = extract_data(history_response)
    assert len(messages) >= 4  # 2 user messages + 2 assistant responses
    
    # Verify message structure
    user_messages = [msg for msg in messages if msg["role"] == "user"]
    assistant_messages = [msg for msg in messages if msg["role"] == "assistant"]
    
    assert len(user_messages) == 2
    assert len(assistant_messages) == 2
    
    # User Workflow Step 6: User lists their conversations
    user_convs_response = await client.get("/api/v1/chat/conversations", headers=auth_headers)
    assert user_convs_response.status_code in [200, 201], f"List conversations failed: {user_convs_response.status_code} - {getattr(user_convs_response, 'text', '')}"

    conversations = extract_data(user_convs_response)
    user_conv_titles = [conv["title"] for conv in conversations]
    assert "My Research Chat" in user_conv_titles
    
    return True


async def test_error_recovery_workflow(client, db, auth_headers):
    """
    Test error recovery scenarios that can occur in production workflows.
    """
    # Setup valid model configuration
    provider_data = {
        "name": "Error Recovery Test Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-error-recovery-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code in [200, 201], f"Provider creation failed: {provider_response.status_code} - {getattr(provider_response, 'text', '')}"
    provider_id = extract_data(provider_response)["id"]

    model_data = {
        "model_name": "gpt-4-error-recovery",
        "display_name": "GPT-4 Error Recovery",
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
    assert model_response.status_code in [200, 201], f"Model creation failed: {model_response.status_code} - {getattr(model_response, 'text', '')}"

    config_data = {
        "name": "Error Recovery Assistant",
        "description": "Model config for error recovery testing",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201], f"Model configuration creation failed: {config_response.status_code} - {getattr(config_response, 'text', '')}"
    model_config_id = extract_data(config_response)["id"]

    # Error Scenario 1: Try to create conversation with invalid model config
    invalid_conv_data = {
        "title": "Invalid Config Conversation",
        "model_configuration_id": "invalid-config-id"
    }
    
    invalid_response = await client.post("/api/v1/chat/conversations", json=invalid_conv_data, headers=auth_headers)
    assert invalid_response.status_code in [400, 404], "Should reject invalid model config ID"
    
    # Error Scenario 2: Create valid conversation
    valid_conv_data = {
        "title": "Valid Error Recovery Conversation",
        "model_configuration_id": model_config_id
    }
    
    conv_response = await client.post("/api/v1/chat/conversations", json=valid_conv_data, headers=auth_headers)
    assert conv_response.status_code in [200, 201], f"Conversation creation failed: {conv_response.status_code} - {getattr(conv_response, 'text', '')}"
    conversation_id = extract_data(conv_response)["id"]

    # Error Scenario 3: Send message with invalid parameters
    invalid_message_data = {
        "message": "",  # Empty message
        "rag_rewrite_mode": "invalid",  # Invalid boolean
    }
    
    invalid_msg_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=invalid_message_data,
        headers=auth_headers
    )
    try:
        assert invalid_msg_response.status_code in [400, 422], "Should reject invalid message data"
    finally:
        try:
            if hasattr(invalid_msg_response, "aclose"):
                await invalid_msg_response.aclose()
            else:
                invalid_msg_response.close()
        except Exception:
            pass
    
    # Error Scenario 4: Send valid message (should work despite previous errors)
    valid_message_data = {
        "message": "This is a valid message after error recovery",
        "rag_rewrite_mode": "no_rag",
    }
    
    valid_msg_response = await client.post(
        f"/api/v1/chat/conversations/{conversation_id}/send",
        json=valid_message_data,
        headers=auth_headers
    )
    try:
        assert valid_msg_response.status_code in [200, 201], f"Should recover and process valid message. Got {valid_msg_response.status_code} - {getattr(valid_msg_response, 'text', '')}"
    finally:
        try:
            if hasattr(valid_msg_response, "aclose"):
                await valid_msg_response.aclose()
            else:
                valid_msg_response.close()
        except Exception:
            pass

    # Error Scenario 5: Deactivate model configuration and try to use it
    deactivate_data = {"is_active": False}
    await client.patch(f"/api/v1/model-configurations/{model_config_id}", json=deactivate_data, headers=auth_headers)
    
    # Try to create new conversation with deactivated config
    deactivated_conv_data = {
        "title": "Deactivated Config Conversation",
        "model_configuration_id": model_config_id
    }
    
    deactivated_response = await client.post("/api/v1/chat/conversations", json=deactivated_conv_data, headers=auth_headers)
    # Should either reject or handle gracefully
    assert deactivated_response.status_code in [200, 400, 422]
    
    return True


async def test_concurrent_user_workflow(client, db, auth_headers):
    """
    Test concurrent usage scenarios to ensure system handles multiple users properly.
    """
    # Setup model configuration
    provider_data = {
        "name": "Concurrent Test Provider",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": "test-concurrent-key",
        "is_active": True
    }
    
    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    provider_id = extract_data(provider_response)["id"]

    model_data = {
        "model_name": "gpt-4-concurrent",
        "display_name": "GPT-4 Concurrent",
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
    assert model_response.status_code in [200, 201]

    config_data = {
        "name": "Concurrent Test Assistant",
        "description": "Model config for concurrent testing",
        "llm_provider_id": provider_id,
        "model_name": model_data["model_name"],
        "is_active": True,
        "created_by": "test-admin"
    }
    
    config_response = await client.post("/api/v1/model-configurations", json=config_data, headers=auth_headers)
    assert config_response.status_code in [200, 201]
    model_config_id = extract_data(config_response)["id"]

    # Create multiple conversations concurrently
    async def create_conversation(conv_num):
        conv_data = {
            "title": f"Concurrent Test Conversation {conv_num}",
            "model_configuration_id": model_config_id
        }
        
        response = await client.post("/api/v1/chat/conversations", json=conv_data, headers=auth_headers)
        return response.status_code in [200, 201], extract_data(response).get("id")

    # Test concurrent conversation creation
    tasks = [create_conversation(i) for i in range(5)]
    results = await asyncio.gather(*tasks)
    
    successful_creations = [result[0] for result in results]
    conversation_ids = [result[1] for result in results if result[1]]
    
    assert all(successful_creations), "All concurrent conversation creations should succeed"
    assert len(conversation_ids) == 5, "Should create 5 conversations"
    
    # Test concurrent message sending
    async def send_message(conv_id, msg_num):
        message_data = {
            "message": f"Concurrent test message {msg_num}",
            "rag_rewrite_mode": "no_rag",
        }
        
        response = await client.post(
            f"/api/v1/chat/conversations/{conv_id}/send",
            json=message_data,
            headers=auth_headers
        )
        try:
            return response.status_code in [200, 201]
        finally:
            try:
                if hasattr(response, "aclose"):
                    await response.aclose()
                else:
                    response.close()
            except Exception:
                pass

    # Send messages to all conversations concurrently
    message_tasks = [send_message(conv_id, i) for i, conv_id in enumerate(conversation_ids)]
    message_results = await asyncio.gather(*message_tasks)
    
    successful_messages = sum(message_results)
    assert successful_messages >= 3, f"At least 3 out of 5 concurrent messages should succeed, got {successful_messages}"
    
    return True


class EndToEndWorkflowTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for end-to-end workflows."""

    def get_test_functions(self) -> List[Callable]:
        """Return end-to-end workflow test functions."""
        return [
            test_complete_admin_setup_workflow,
            test_complete_user_chat_workflow,
            test_error_recovery_workflow,
            test_concurrent_user_workflow,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "End-to-End Workflows"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Complete workflow integration tests covering admin setup, user chat flows, error recovery, and concurrent usage scenarios"


if __name__ == "__main__":
    import asyncio
    suite = EndToEndWorkflowTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys
    sys.exit(exit_code)
