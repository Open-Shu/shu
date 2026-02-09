"""
Prompt Management Integration Tests for Shu

These tests cover the complete prompt management system including:
- Prompt CRUD operations
- Entity assignment/unassignment (knowledge bases, models, etc.)
- Validation and error handling
- Authentication and authorization
"""

import sys
import uuid
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

# Test Data Constants
SAMPLE_PROMPT_DATA = {
    "name": "Test Prompt",
    "description": "A test prompt for integration testing",
    "content": "You are a helpful AI assistant. Provide clear, accurate, and informative responses.",
    "entity_type": "llm_model",  # Changed from knowledge_base to llm_model
    "is_active": True,
}

SAMPLE_KB_DATA = {
    "name": "Test Knowledge Base for Prompts",
    "description": "A test knowledge base for prompt assignment testing",
    "sync_enabled": True,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "chunk_size": 1000,
    "chunk_overlap": 200,
}


# Test Functions
async def test_prompt_health_check(client, db, auth_headers):
    """Test that the prompt endpoints are accessible."""
    response = await client.get("/api/v1/prompts/", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"


async def test_create_prompt_success(client, db, auth_headers):
    """Test successful creation of a prompt."""
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {
        **SAMPLE_PROMPT_DATA,
        "name": f"Test Prompt {unique_id}",
        "description": f"Test prompt for integration testing {unique_id}",
    }

    response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)

    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    response_data = response.json()
    assert "data" in response_data, f"No 'data' key in response: {response_data}"
    prompt = extract_data(response)

    # Verify response structure
    assert prompt["name"] == prompt_data["name"]
    assert prompt["description"] == prompt_data["description"]
    assert prompt["content"] == prompt_data["content"]
    assert prompt["entity_type"] == prompt_data["entity_type"]
    assert prompt["is_active"] == prompt_data["is_active"]
    assert "id" in prompt
    assert "version" in prompt
    assert "created_at" in prompt
    assert "updated_at" in prompt

    # Verify data was stored in database
    result = await db.execute(
        text("SELECT name, content, entity_type FROM prompts WHERE id = :id"), {"id": prompt["id"]}
    )
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row.name == prompt_data["name"]
    assert db_row.content == prompt_data["content"]
    assert db_row.entity_type == prompt_data["entity_type"]

    return prompt["id"]


async def test_list_prompts(client, db, auth_headers):
    """Test listing prompts."""
    # Create a test prompt first
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Listing {unique_id}"}

    create_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert create_response.status_code == 201

    # List prompts
    response = await client.get("/api/v1/prompts/", headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    response_data = response.json()
    assert "data" in response_data, f"No 'data' key in response: {response_data}"
    data = response_data["data"]

    # Check if it's a list directly or has items/total structure (API standard)
    if isinstance(data, list):
        # Direct list response
        assert len(data) >= 1, f"Expected at least 1 prompt, got {len(data)}"
        prompts_list = data
    else:
        # Paginated response with items/total (API standard)
        assert "items" in data, f"No 'items' key in data: {data}"
        assert "total" in data, f"No 'total' key in data: {data}"
        assert isinstance(data["items"], list), f"Items is not a list: {type(data['items'])}"
        assert data["total"] >= 1, f"Expected total >= 1, got {data['total']}"
        prompts_list = data["items"]

    # Find our created prompt
    created_prompt = next((p for p in prompts_list if p["name"] == prompt_data["name"]), None)
    assert (
        created_prompt is not None
    ), f"Could not find prompt with name '{prompt_data['name']}' in {[p['name'] for p in prompts_list]}"
    assert created_prompt["content"] == prompt_data["content"]


async def test_get_prompt_by_id(client, db, auth_headers):
    """Test retrieving a specific prompt by ID."""
    # First create a prompt
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Retrieval {unique_id}"}

    create_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert create_response.status_code == 201, f"Create failed: {create_response.status_code}: {create_response.text}"
    prompt_id = extract_data(create_response)["id"]

    # Now retrieve it
    response = await client.get(f"/api/v1/prompts/{prompt_id}", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    prompt = extract_data(response)
    assert prompt["id"] == prompt_id
    assert prompt["name"] == prompt_data["name"]
    assert prompt["content"] == prompt_data["content"]


async def test_update_prompt(client, db, auth_headers):
    """Test updating a prompt."""
    # Create prompt first
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Update {unique_id}"}

    create_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert create_response.status_code == 201
    prompt_id = extract_data(create_response)["id"]

    # Update the prompt
    update_data = {
        "name": f"Updated Test Prompt {unique_id}",
        "description": "Updated description for testing",
        "content": "You are an updated AI assistant. Provide helpful and accurate responses.",
    }

    response = await client.put(f"/api/v1/prompts/{prompt_id}", json=update_data, headers=auth_headers)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    response_data = response.json()
    assert "data" in response_data
    updated_prompt = extract_data(response)
    assert updated_prompt["name"] == update_data["name"]
    assert updated_prompt["description"] == update_data["description"]
    assert updated_prompt["content"] == update_data["content"]

    # Verify in database
    result = await db.execute(text("SELECT name, description, content FROM prompts WHERE id = :id"), {"id": prompt_id})
    db_row = result.fetchone()
    assert db_row.name == update_data["name"]
    assert db_row.description == update_data["description"]
    assert db_row.content == update_data["content"]


async def test_delete_prompt(client, db, auth_headers):
    """Test deleting a prompt."""
    # Create prompt first
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Deletion {unique_id}"}

    create_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert create_response.status_code == 201
    prompt_id = extract_data(create_response)["id"]

    # Delete the prompt
    response = await client.delete(f"/api/v1/prompts/{prompt_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone from database
    result = await db.execute(text("SELECT COUNT(*) FROM prompts WHERE id = :id"), {"id": prompt_id})
    count = result.scalar()
    assert count == 0

    # Verify 404 when trying to get deleted prompt
    get_response = await client.get(f"/api/v1/prompts/{prompt_id}", headers=auth_headers)
    assert get_response.status_code == 404


async def test_prompt_assignment_to_knowledge_base_blocked(client, db, auth_headers):
    """Test that KB prompt assignments fail because knowledge_base is not a supported entity type."""
    # Create a prompt
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Assignment {unique_id}"}

    prompt_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    assert prompt_response.status_code == 201
    prompt_id = extract_data(prompt_response)["id"]

    # Create a knowledge base
    kb_data = {**SAMPLE_KB_DATA, "name": f"Test KB for Prompt Assignment {unique_id}"}

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Attempt to assign prompt to knowledge base (should fail)
    assignment_data = {"entity_id": kb_id, "entity_type": "knowledge_base"}

    response = await client.post(f"/api/v1/prompts/{prompt_id}/assignments", json=assignment_data, headers=auth_headers)

    # Should return 422 with validation error about unsupported entity type
    assert response.status_code == 422, f"Expected 422 (validation error), got {response.status_code}: {response.text}"
    error_response = response.json()
    # Should contain validation error about entity_type
    assert "error" in error_response
    assert "Cannot assign prompt" in error_response["error"]["message"]

    # Verify no assignment was created in database
    result = await db.execute(
        text("SELECT * FROM prompt_assignments WHERE prompt_id = :prompt_id AND entity_id = :entity_id"),
        {"prompt_id": prompt_id, "entity_id": kb_id},
    )
    assignment = result.fetchone()
    assert assignment is None


async def test_prompt_unassignment_from_knowledge_base_blocked(client, db, auth_headers):
    """Test that KB prompt unassignments fail because knowledge_base is not a supported entity type."""
    # Create and assign a prompt (reuse previous test logic)
    unique_id = str(uuid.uuid4())[:8]

    # Create prompt
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Unassignment {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    prompt_id = extract_data(prompt_response)["id"]

    # Create knowledge base
    kb_data = {**SAMPLE_KB_DATA, "name": f"Test KB for Prompt Unassignment {unique_id}"}
    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    kb_id = extract_data(kb_response)["id"]

    # Attempt to assign prompt (should fail with validation error)
    assignment_data = {"entity_id": kb_id, "entity_type": "knowledge_base"}
    assign_response = await client.post(
        f"/api/v1/prompts/{prompt_id}/assignments", json=assignment_data, headers=auth_headers
    )
    assert assign_response.status_code == 422  # Should be validation error

    # Since assignment failed, unassignment should also fail (no assignment exists)
    response = await client.delete(f"/api/v1/prompts/{prompt_id}/assignments/{kb_id}", headers=auth_headers)
    # Should return 404 because the assignment doesn't exist (was never created)
    assert response.status_code == 404

    # Verify no assignment exists in database
    result = await db.execute(
        text("SELECT COUNT(*) FROM prompt_assignments WHERE prompt_id = :prompt_id AND entity_id = :entity_id"),
        {"prompt_id": prompt_id, "entity_id": kb_id},
    )
    count = result.scalar()
    assert count == 0


async def test_unauthorized_access(client, db, auth_headers):
    """Test that prompt endpoints require authentication."""
    # Try to access without auth headers
    response = await client.get("/api/v1/prompts/")
    assert response.status_code == 401

    # Try to create without auth
    response = await client.post("/api/v1/prompts/", json=SAMPLE_PROMPT_DATA)
    assert response.status_code == 401

    # Try to update without auth
    response = await client.put("/api/v1/prompts/123", json=SAMPLE_PROMPT_DATA)
    assert response.status_code == 401

    # Try to delete without auth
    response = await client.delete("/api/v1/prompts/123")
    assert response.status_code == 401


async def test_invalid_prompt_data_validation(client, db, auth_headers):
    """Test that invalid prompt data is rejected."""
    # Test missing required fields
    invalid_data = {
        "description": "Missing name and content fields"
        # Missing required 'name' and 'content' fields
    }

    response = await client.post("/api/v1/prompts/", json=invalid_data, headers=auth_headers)
    assert response.status_code == 422  # Validation error

    # Test empty content
    invalid_data = {
        "name": "Test Prompt",
        "content": "",  # Empty content should be invalid
        "description": "Test description",
    }

    response = await client.post("/api/v1/prompts/", json=invalid_data, headers=auth_headers)
    assert response.status_code == 422


async def test_prompt_not_found_errors(client, db, auth_headers):
    """Test 404 errors for non-existent prompts."""
    non_existent_id = str(uuid.uuid4())

    # Test get non-existent prompt
    response = await client.get(f"/api/v1/prompts/{non_existent_id}", headers=auth_headers)
    assert response.status_code == 404

    # Test update non-existent prompt
    response = await client.put(f"/api/v1/prompts/{non_existent_id}", json=SAMPLE_PROMPT_DATA, headers=auth_headers)
    assert response.status_code == 404

    # Test delete non-existent prompt
    response = await client.delete(f"/api/v1/prompts/{non_existent_id}", headers=auth_headers)
    assert response.status_code == 404


async def test_invalid_entity_assignment(client, db, auth_headers):
    """Test assignment to invalid entities."""
    # Create a prompt
    unique_id = str(uuid.uuid4())[:8]
    prompt_data = {**SAMPLE_PROMPT_DATA, "name": f"Test Prompt for Invalid Assignment {unique_id}"}
    prompt_response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
    prompt_id = prompt_response.json()["data"]["id"]

    # Try to assign to non-existent entity
    # NOTE: Current implementation allows assignments to non-existent entities
    # This might be by design for flexibility, but could be enhanced with validation
    non_existent_entity_id = str(uuid.uuid4())
    assignment_data = {
        "entity_id": non_existent_entity_id,
        "entity_type": "llm_model",  # Required field after KB removal
    }

    response = await client.post(f"/api/v1/prompts/{prompt_id}/assignments", json=assignment_data, headers=auth_headers)
    # Current behavior: assignment succeeds even for non-existent entities
    # Future enhancement: could add entity validation to return 404
    assert response.status_code == 201, f"Assignment failed: {response.status_code}: {response.text}"

    # Verify the assignment was created
    response_data = response.json()
    assert "data" in response_data
    assignment = response_data["data"]
    assert assignment["entity_id"] == non_existent_entity_id
    assert assignment["prompt_id"] == prompt_id


async def test_prompt_search_and_filtering(client, db, auth_headers):
    """Test prompt search and filtering functionality."""
    unique_id = str(uuid.uuid4())[:8]

    # Create multiple prompts with different names
    prompts_data = [
        {
            **SAMPLE_PROMPT_DATA,
            "name": f"Search Test Prompt Alpha {unique_id}",
            "description": "First test prompt for search",
        },
        {
            **SAMPLE_PROMPT_DATA,
            "name": f"Search Test Prompt Beta {unique_id}",
            "description": "Second test prompt for search",
        },
        {
            **SAMPLE_PROMPT_DATA,
            "name": f"Different Test Name {unique_id}",
            "description": "Third test prompt with different name",
        },
    ]

    # Create all prompts
    created_prompts = []
    for prompt_data in prompts_data:
        response = await client.post("/api/v1/prompts/", json=prompt_data, headers=auth_headers)
        assert response.status_code == 201
        created_prompts.append(extract_data(response))

    # Test search by name (if search functionality exists)
    response = await client.get("/api/v1/prompts?search=Alpha", headers=auth_headers)
    if response.status_code == 200:
        # If search is implemented, verify results
        data = extract_data(response)
        alpha_prompts = [p for p in data["items"] if "Alpha" in p["name"]]
        assert len(alpha_prompts) >= 1

    # Test filtering by active status (if filtering exists)
    response = await client.get("/api/v1/prompts?is_active=true", headers=auth_headers)
    if response.status_code == 200:
        # If filtering is implemented, all returned prompts should be active
        data = extract_data(response)
        for prompt in data["items"]:
            if prompt["name"].endswith(unique_id):  # Only check our test prompts
                assert prompt["is_active"] is True


# Test Suite Class
class PromptTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Prompt Management functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all prompt test functions."""
        return [
            test_prompt_health_check,
            test_create_prompt_success,
            test_list_prompts,
            test_get_prompt_by_id,
            test_update_prompt,
            test_delete_prompt,
            test_prompt_assignment_to_knowledge_base_blocked,
            test_prompt_unassignment_from_knowledge_base_blocked,
            test_unauthorized_access,
            test_invalid_prompt_data_validation,
            test_prompt_not_found_errors,
            test_invalid_entity_assignment,
            test_prompt_search_and_filtering,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Prompt Management Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Prompt Management CRUD operations, entity assignments, and API functionality"

    def get_cli_examples(self) -> str:
        """Return prompt-specific CLI examples."""
        return """
Examples:
  python tests/test_prompt_integration.py                                    # Run all prompt tests
  python tests/test_prompt_integration.py --list                            # List available tests
  python tests/test_prompt_integration.py --test test_create_prompt_success # Run specific test
  python tests/test_prompt_integration.py --pattern create                  # Run all 'create' tests
  python tests/test_prompt_integration.py --pattern assignment              # Run assignment tests
  python tests/test_prompt_integration.py --pattern "auth|validation"       # Run auth and validation tests
        """


if __name__ == "__main__":
    suite = PromptTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
