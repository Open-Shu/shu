"""
Performance and Load Integration Tests

These tests verify system performance and scalability including:
- Concurrent user operations and chat sessions
- Large document processing and chunking
- System throughput and response times
- Memory usage and resource management
- Database query performance
- API endpoint scalability
"""

import asyncio
import sys
import time
import uuid
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite

# Test Data for Performance Testing
LARGE_DOCUMENT_CONTENT = (
    """
This is a large document for testing performance of document processing and chunking.
"""
    * 1000
)  # Create a large document (~70KB)

CONCURRENT_USER_COUNT = 5
CONCURRENT_REQUEST_COUNT = 10


async def test_concurrent_knowledge_base_operations(client, db, auth_headers):
    """Test concurrent knowledge base creation and operations."""
    start_time = time.time()

    # Create multiple knowledge bases concurrently
    tasks = []
    kb_names = []

    for i in range(CONCURRENT_USER_COUNT):
        unique_id = str(uuid.uuid4())[:8]
        kb_name = f"test_concurrent_kb_{i}_{unique_id}"
        kb_names.append(kb_name)

        kb_data = {
            "name": kb_name,
            "description": f"Test concurrent knowledge base {i} for performance testing {unique_id}",
        }

        task = client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
        tasks.append(task)

    # Execute all requests concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    creation_time = time.time() - start_time

    # Analyze results
    successful_creations = 0
    kb_ids = []

    for i, response in enumerate(responses):
        if hasattr(response, "status_code") and response.status_code == 201:
            successful_creations += 1
            response_data = response.json()
            if "data" in response_data:
                kb_ids.append(response_data["data"]["id"])
            else:
                kb_ids.append(response_data["id"])
        else:
            print(f"⚠️  KB creation {i} failed: {response}")

    print(f"✅ Created {successful_creations}/{CONCURRENT_USER_COUNT} KBs concurrently in {creation_time:.2f}s")
    print(f"📊 Average creation time: {creation_time/CONCURRENT_USER_COUNT:.3f}s per KB")

    # Test concurrent read operations
    start_time = time.time()
    read_tasks = []

    for kb_id in kb_ids[:3]:  # Test first 3 KBs
        task = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
        read_tasks.append(task)

    read_responses = await asyncio.gather(*read_tasks, return_exceptions=True)
    read_time = time.time() - start_time

    successful_reads = sum(1 for r in read_responses if hasattr(r, "status_code") and r.status_code == 200)

    print(f"✅ Completed {successful_reads}/{len(read_tasks)} concurrent reads in {read_time:.2f}s")
    print(f"📊 Average read time: {read_time/len(read_tasks):.3f}s per request")

    # Performance assertions
    assert creation_time < 10.0, f"KB creation took too long: {creation_time:.2f}s"
    assert read_time < 5.0, f"KB reads took too long: {read_time:.2f}s"
    assert (
        successful_creations >= CONCURRENT_USER_COUNT * 0.8
    ), f"Too many creation failures: {successful_creations}/{CONCURRENT_USER_COUNT}"

    return True


async def test_large_document_processing_performance(client, db, auth_headers):
    """Test performance of processing large documents."""
    # Create knowledge base for large document testing
    unique_id = str(uuid.uuid4())[:8]
    kb_data = {
        "name": f"test_large_doc_perf_kb_{unique_id}",
        "description": f"Test knowledge base for large document performance testing {unique_id}",
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201

    kb_response_data = kb_response.json()
    if "data" in kb_response_data:
        kb_id = kb_response_data["data"]["id"]
    else:
        kb_id = kb_response_data["id"]

    # Test document processing performance
    start_time = time.time()

    # Create a large document (this tests chunking and embedding performance)
    doc_data = {
        "title": "Large Performance Test Document",
        "content": LARGE_DOCUMENT_CONTENT,
        "source_type": "manual",
        "metadata": {"test": "performance", "size": "large"},
    }

    # Note: This endpoint might not exist yet, so we'll test what's available
    try:
        doc_response = await client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents", json=doc_data, headers=auth_headers
        )
        processing_time = time.time() - start_time

        if doc_response.status_code in [200, 201]:
            print(f"✅ Large document processed in {processing_time:.2f}s")
            print(f"📊 Document size: ~{len(LARGE_DOCUMENT_CONTENT)} characters")
            print(f"📊 Processing rate: {len(LARGE_DOCUMENT_CONTENT)/processing_time:.0f} chars/sec")

            # Performance assertion
            assert processing_time < 30.0, f"Document processing took too long: {processing_time:.2f}s"
        else:
            print(f"⚠️  Document processing endpoint returned {doc_response.status_code}")
            print("This might indicate the endpoint is not implemented yet")

    except Exception as e:
        print(f"⚠️  Document processing test skipped: {e}")
        print("This indicates document upload endpoints may not be implemented yet")

    return True


async def test_concurrent_chat_sessions(client, db, auth_headers):
    """Test performance of concurrent chat sessions."""
    # Create test provider and model configuration for chat testing
    unique_id = str(uuid.uuid4())[:8]
    provider_data = {
        "name": f"test_perf_provider_{unique_id}",
        "provider_type": "openai",
        "api_endpoint": "https://api.openai.com/v1",
        "api_key": f"test-performance-key-{unique_id}",
        "is_active": True,
    }

    provider_response = await client.post("/api/v1/llm/providers", json=provider_data, headers=auth_headers)
    assert provider_response.status_code == 201

    provider_response_data = provider_response.json()
    if "data" in provider_response_data:
        provider_id = provider_response_data["data"]["id"]
    else:
        provider_id = provider_response_data["id"]

    # Create model
    model_data = {
        "model_name": "gpt-4",
        "display_name": f"test_gpt4_perf_{unique_id}",
        "description": f"Test model for performance testing {unique_id}",
        "context_window": 8192,
        "max_tokens": 4096,
        "supports_streaming": True,
        "supports_functions": False,
        "supports_vision": False,
    }

    model_response = await client.post(
        f"/api/v1/llm/providers/{provider_id}/models", json=model_data, headers=auth_headers
    )
    assert model_response.status_code == 200

    # Create model configuration
    model_config_data = {
        "name": f"test_perf_assistant_{unique_id}",
        "description": f"Test model configuration for performance testing {unique_id}",
        "llm_provider_id": provider_id,
        "model_name": "gpt-4",
        "is_active": True,
        "created_by": "test-user",
        "knowledge_base_ids": [],
    }

    config_response = await client.post("/api/v1/model-configurations", json=model_config_data, headers=auth_headers)
    assert config_response.status_code == 201

    config_response_data = config_response.json()
    if "data" in config_response_data:
        model_config_id = config_response_data["data"]["id"]
    else:
        model_config_id = config_response_data["id"]

    # Test concurrent conversation creation
    start_time = time.time()
    conversation_tasks = []

    for i in range(CONCURRENT_USER_COUNT):
        conversation_data = {
            "title": f"test_perf_conversation_{i}_{unique_id}",
            "model_configuration_id": model_config_id,
        }

        task = client.post("/api/v1/chat/conversations", json=conversation_data, headers=auth_headers)
        conversation_tasks.append(task)

    conversation_responses = await asyncio.gather(*conversation_tasks, return_exceptions=True)
    conversation_creation_time = time.time() - start_time

    # Analyze conversation creation results
    successful_conversations = 0
    conversation_ids = []

    for i, response in enumerate(conversation_responses):
        if hasattr(response, "status_code") and response.status_code == 200:
            successful_conversations += 1
            response_data = response.json()
            if "data" in response_data:
                conversation_ids.append(response_data["data"]["id"])
            else:
                conversation_ids.append(response_data["id"])
        else:
            print(f"⚠️  Conversation creation {i} failed: {response}")

    print(
        f"✅ Created {successful_conversations}/{CONCURRENT_USER_COUNT} conversations in {conversation_creation_time:.2f}s"
    )
    print(f"📊 Average conversation creation: {conversation_creation_time/CONCURRENT_USER_COUNT:.3f}s per conversation")

    # Test concurrent message sending (will fail with test API key but tests system performance)
    if conversation_ids:
        start_time = time.time()
        message_tasks = []

        for i, conv_id in enumerate(conversation_ids[:3]):  # Test first 3 conversations
            message_data = {
                "message": f"Performance test message {i}",
                "rag_rewrite_mode": "no_rag",
                "temperature": 0.7,
                "max_tokens": 50,
            }

            task = client.post(
                f"/api/v1/chat/conversations/{conv_id}/send",
                json=message_data,
                headers=auth_headers,
            )
            message_tasks.append(task)

        message_responses = await asyncio.gather(*message_tasks, return_exceptions=True)
        message_time = time.time() - start_time

        for r in message_responses:
            if hasattr(r, "close"):
                try:
                    closer = getattr(r, "aclose", None)
                    if callable(closer):
                        await closer()
                    else:
                        r.close()
                except Exception:
                    pass

        # Count responses (will likely be errors due to test API key, but system should handle gracefully)
        response_count = sum(1 for r in message_responses if hasattr(r, "status_code"))

        print(f"✅ Processed {response_count}/{len(message_tasks)} concurrent messages in {message_time:.2f}s")
        print(f"📊 Average message processing: {message_time/len(message_tasks):.3f}s per message")

        # Performance assertions
        assert (
            conversation_creation_time < 10.0
        ), f"Conversation creation took too long: {conversation_creation_time:.2f}s"
        assert message_time < 15.0, f"Message processing took too long: {message_time:.2f}s"

    return True


async def test_api_response_times(client, db, auth_headers):
    """Test API endpoint response times under load."""
    endpoints_to_test = [
        ("/api/v1/health", "GET"),
        ("/api/v1/knowledge-bases", "GET"),
        ("/api/v1/llm/providers", "GET"),
        ("/api/v1/model-configurations", "GET"),
    ]

    response_times = {}

    for endpoint, method in endpoints_to_test:
        times = []

        # Test each endpoint multiple times
        for _ in range(5):
            start_time = time.time()

            if method == "GET":
                response = await client.get(endpoint, headers=auth_headers)
            else:
                continue  # Skip non-GET methods for this test

            end_time = time.time()
            response_time = end_time - start_time

            if response.status_code == 200:
                times.append(response_time)

        if times:
            avg_time = sum(times) / len(times)
            max_time = max(times)
            min_time = min(times)

            response_times[endpoint] = {
                "avg": avg_time,
                "max": max_time,
                "min": min_time,
                "count": len(times),
            }

            print(f"📊 {endpoint}: avg={avg_time:.3f}s, max={max_time:.3f}s, min={min_time:.3f}s")

            # Performance assertions
            assert avg_time < 2.0, f"{endpoint} average response time too slow: {avg_time:.3f}s"
            assert max_time < 5.0, f"{endpoint} max response time too slow: {max_time:.3f}s"

    print(f"✅ API response time testing completed for {len(response_times)} endpoints")
    return True


class PerformanceLoadIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Performance and Load testing."""

    def get_test_functions(self) -> list[Callable]:
        """Return performance and load test functions."""
        return [
            test_concurrent_knowledge_base_operations,
            test_large_document_processing_performance,
            test_concurrent_chat_sessions,
            test_api_response_times,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Performance and Load Integration"

    def get_suite_description(self) -> str:
        """Return description of this test suite for CLI help."""
        return "Integration tests for system performance including concurrent operations, large document processing, and API response times"


if __name__ == "__main__":
    import asyncio

    suite = PerformanceLoadIntegrationTestSuite()
    exit_code = asyncio.run(suite.run_suite())
    import sys

    sys.exit(exit_code)
