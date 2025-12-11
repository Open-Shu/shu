"""
Knowledge Base Integration Tests for Shu

These tests cover knowledge base CRUD operations, document management,
and the complete knowledge base lifecycle.
"""

import sys
import os
import logging
from typing import List, Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_authentication_errors,
    expect_validation_errors,
    expect_duplicate_errors,
    ExpectedErrorContext
)
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def test_health_endpoint(client, db, auth_headers):
    """Test that the health endpoint is accessible."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["status"] in ["healthy", "warning"]


async def test_list_knowledge_bases_structure(client, db, auth_headers):
    """Test that the knowledge base list API returns the correct structure."""
    response = await client.get("/api/v1/knowledge-bases", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]

    # Verify response structure
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "size" in data
    assert "pages" in data

    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)
    assert data["total"] >= 0  # Could be 0 or more
    assert data["page"] >= 1
    assert data["size"] > 0


async def test_create_knowledge_base_success(client, db, auth_headers):
    """Test successful knowledge base creation."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Test Knowledge Base {unique_id}",
        "description": "A test knowledge base for integration testing",
        "sync_enabled": True,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "chunk_size": 1000,
        "chunk_overlap": 200
    }

    response = await client.post("/api/v1/knowledge-bases",
                                json=kb_data,
                                headers=auth_headers)
    assert response.status_code == 201

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["name"] == kb_data["name"]
    assert data["description"] == kb_data["description"]
    assert data["sync_enabled"] == kb_data["sync_enabled"]
    assert data["embedding_model"] == kb_data["embedding_model"]
    assert data["chunk_size"] == kb_data["chunk_size"]
    assert data["chunk_overlap"] == kb_data["chunk_overlap"]
    assert "id" in data
    assert "created_at" in data
    assert "status" in data

    # Verify in database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"),
                             {"id": data["id"]})
    kb_record = result.fetchone()
    assert kb_record is not None
    assert kb_record.name == kb_data["name"]


async def test_get_knowledge_base_by_id(client, db, auth_headers):
    """Test retrieving a knowledge base by ID."""
    import uuid
    unique_id = str(uuid.uuid4())[:8]

    # First create a knowledge base
    kb_data = {
        "name": f"Test KB for Retrieval {unique_id}",
        "description": "Testing retrieval functionality",
        "sync_enabled": True
    }

    create_response = await client.post("/api/v1/knowledge-bases",
                                       json=kb_data,
                                       headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Now retrieve it
    response = await client.get(f"/api/v1/knowledge-bases/{kb_id}",
                               headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["id"] == kb_id
    assert data["name"] == kb_data["name"]
    assert data["description"] == kb_data["description"]
    assert data["sync_enabled"] == kb_data["sync_enabled"]


async def test_update_knowledge_base(client, db, auth_headers):
    """Test updating a knowledge base."""
    # Create a knowledge base
    kb_data = {
        "name": "Original Test KB Name",
        "description": "Original description",
        "sync_enabled": True,
        "chunk_size": 1000
    }

    create_response = await client.post("/api/v1/knowledge-bases",
                                       json=kb_data,
                                       headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Update it
    update_data = {
        "name": "Updated Test KB Name",
        "description": "Updated description",
        "sync_enabled": False,
        "chunk_size": 1500
    }

    response = await client.put(f"/api/v1/knowledge-bases/{kb_id}",
                               json=update_data,
                               headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["name"] == update_data["name"]
    assert data["description"] == update_data["description"]
    assert data["sync_enabled"] == update_data["sync_enabled"]
    assert data["chunk_size"] == update_data["chunk_size"]

    # Verify in database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"),
                             {"id": kb_id})
    kb_record = result.fetchone()
    assert kb_record.name == update_data["name"]


async def test_delete_knowledge_base(client, db, auth_headers):
    """Test deleting a knowledge base."""
    # Create a knowledge base
    kb_data = {
        "name": "KB to Delete",
        "description": "This will be deleted",
        "sync_enabled": True
    }

    create_response = await client.post("/api/v1/knowledge-bases",
                                       json=kb_data,
                                       headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Delete it
    response = await client.delete(f"/api/v1/knowledge-bases/{kb_id}",
                                  headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone from database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"),
                             {"id": kb_id})
    kb_record = result.fetchone()
    assert kb_record is None

    # Verify 404 on subsequent GET
    get_response = await client.get(f"/api/v1/knowledge-bases/{kb_id}",
                                   headers=auth_headers)
    assert get_response.status_code == 404


async def test_create_knowledge_base_duplicate_name(client, db, auth_headers):
    """Test that duplicate knowledge base names are handled appropriately."""
    logger.info("=== EXPECTED TEST OUTPUT: Testing duplicate knowledge base name handling ===")

    import uuid
    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Duplicate Name Test {unique_id}",
        "description": "First KB with this name",
        "sync_enabled": True
    }

    # Create first KB
    response1 = await client.post("/api/v1/knowledge-bases",
                                 json=kb_data,
                                 headers=auth_headers)
    assert response1.status_code == 201

    with expect_duplicate_errors():
        # Try to create second KB with same name
        kb_data2 = {
            "name": f"Duplicate Name Test {unique_id}",
            "description": "Second KB with same name",
            "sync_enabled": False
        }

        response2 = await client.post("/api/v1/knowledge-bases",
                                     json=kb_data2,
                                     headers=auth_headers)
        # Should fail since duplicate names aren't allowed
        assert response2.status_code in [400, 500]  # Accept either 400 or 500 for duplicate names

    logger.info("=== EXPECTED TEST OUTPUT: Duplicate name test completed successfully ===")


async def test_create_knowledge_base_invalid_data(client, db, auth_headers):
    """Test knowledge base creation with invalid data."""
    logger.info("=== EXPECTED TEST OUTPUT: The following validation errors (400/422) are expected ===")

    invalid_data_sets = [
        {},  # Empty data
        {"name": ""},  # Empty name
        {"name": "Valid Name", "chunk_size": -1},  # Invalid chunk size
        {"name": "Valid Name", "chunk_overlap": 2000, "chunk_size": 1000},  # Overlap >= chunk_size
    ]

    for i, invalid_data in enumerate(invalid_data_sets):
        response = await client.post("/api/v1/knowledge-bases",
                                    json=invalid_data,
                                    headers=auth_headers)
        assert response.status_code in [400, 422], f"Invalid data should be rejected: {invalid_data}"
        logger.info(f"=== EXPECTED TEST OUTPUT: Validation error {i+1}/4 for invalid data occurred as expected ===")


async def test_unauthorized_access(client, db, auth_headers):
    """Test that knowledge base endpoints require authentication."""
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication errors are expected ===")

    # Test without auth headers
    response = await client.get("/api/v1/knowledge-bases")
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated GET occurred as expected ===")

    response = await client.post("/api/v1/knowledge-bases",
                                json={"name": "Test", "access_level": "RESEARCH"})
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated POST occurred as expected ===")


async def test_knowledge_base_embedding_models(client, db, auth_headers):
    """Test different embedding models for knowledge bases."""
    embedding_models = [
        "sentence-transformers/all-MiniLM-L6-v2",
        "sentence-transformers/all-mpnet-base-v2"
    ]

    for model in embedding_models:
        kb_data = {
            "name": f"Test KB {model.split('/')[-1]}",
            "description": f"Testing {model} embedding model",
            "embedding_model": model,
            "sync_enabled": True
        }

        response = await client.post("/api/v1/knowledge-bases",
                                    json=kb_data,
                                    headers=auth_headers)
        assert response.status_code == 201

        response_data = response.json()
        data = response_data["data"]
        assert data["embedding_model"] == model


class KnowledgeBaseIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for knowledge base functionality."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all knowledge base integration test functions."""
        return [
            test_health_endpoint,
            test_list_knowledge_bases_structure,
            test_create_knowledge_base_success,
            test_get_knowledge_base_by_id,
            test_update_knowledge_base,
            test_delete_knowledge_base,
            test_create_knowledge_base_duplicate_name,
            test_create_knowledge_base_invalid_data,
            test_unauthorized_access,
            test_knowledge_base_embedding_models,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Knowledge Base Integration Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for knowledge base CRUD operations and access control"
    
    def get_cli_examples(self) -> str:
        """Return knowledge base-specific CLI examples."""
        return """
Examples:
  python tests/test_knowledge_base_integration.py                    # Run all KB tests
  python tests/test_knowledge_base_integration.py --list            # List available tests
  python tests/test_knowledge_base_integration.py --test test_create_knowledge_base_success
  python tests/test_knowledge_base_integration.py --pattern "create" # Run creation tests
  python tests/test_knowledge_base_integration.py --pattern "access" # Run access tests
        """


if __name__ == "__main__":
    suite = KnowledgeBaseIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
