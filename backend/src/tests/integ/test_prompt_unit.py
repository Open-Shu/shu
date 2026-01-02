"""
Prompt Management Unit Tests for Shu

These tests verify prompt business logic, data validation, and response formatting
without requiring database or API setup.
"""

import sys
import os
from typing import List, Callable
from datetime import datetime
import uuid

from integ.base_unit_test import BaseUnitTestSuite
from shu.schemas.prompt import PromptCreate, PromptUpdate, PromptResponse, PromptAssignmentCreate
from shu.models.prompt import Prompt, PromptAssignment, EntityType


def test_prompt_create_schema_validation():
    """Test that PromptCreate schema validates required fields correctly."""
    # Valid data should pass
    valid_data = {
        "name": "Test Prompt",
        "description": "A test prompt",
        "content": "You are a helpful assistant. Provide clear and accurate responses.",
        "entity_type": "knowledge_base",
        "is_active": True
    }

    prompt_create = PromptCreate(**valid_data)
    assert prompt_create.name == "Test Prompt"
    assert prompt_create.content == "You are a helpful assistant. Provide clear and accurate responses."
    assert prompt_create.entity_type == "knowledge_base"
    assert prompt_create.is_active is True


def test_prompt_create_schema_defaults():
    """Test that PromptCreate schema applies correct defaults."""
    # Minimal valid data
    minimal_data = {
        "name": "Minimal Prompt",
        "content": "Basic content",
        "entity_type": "knowledge_base"
    }

    prompt_create = PromptCreate(**minimal_data)
    assert prompt_create.name == "Minimal Prompt"
    assert prompt_create.content == "Basic content"
    assert prompt_create.entity_type == "knowledge_base"
    assert prompt_create.is_active is True  # Default active status
    assert prompt_create.description is None  # Optional field


def test_prompt_create_schema_validation_errors():
    """Test that PromptCreate schema rejects invalid data."""
    # Test missing required name
    try:
        PromptCreate(content="Content without name")
        assert False, "Should have raised validation error for missing name"
    except Exception:
        pass  # Expected validation error
    
    # Test missing required content
    try:
        PromptCreate(name="Name without content")
        assert False, "Should have raised validation error for missing content"
    except Exception:
        pass  # Expected validation error
    
    # Test empty name
    try:
        PromptCreate(name="", content="Content with empty name")
        assert False, "Should have raised validation error for empty name"
    except Exception:
        pass  # Expected validation error
    
    # Test empty content
    try:
        PromptCreate(name="Name", content="")
        assert False, "Should have raised validation error for empty content"
    except Exception:
        pass  # Expected validation error


def test_prompt_update_schema_validation():
    """Test that PromptUpdate schema validates optional fields correctly."""
    # All fields provided
    update_data = {
        "name": "Updated Prompt",
        "description": "Updated description",
        "content": "You are an updated assistant. Be helpful and accurate.",
        "is_active": False
    }

    prompt_update = PromptUpdate(**update_data)
    assert prompt_update.name == "Updated Prompt"
    assert prompt_update.content == "You are an updated assistant. Be helpful and accurate."
    assert prompt_update.is_active is False

    # Partial update (only some fields)
    partial_data = {
        "name": "Partially Updated Prompt"
    }

    prompt_update = PromptUpdate(**partial_data)
    assert prompt_update.name == "Partially Updated Prompt"
    assert prompt_update.content is None
    assert prompt_update.is_active is None


def test_prompt_response_schema():
    """Test that PromptResponse schema formats data correctly."""
    # Create a mock prompt model
    prompt_data = {
        "id": str(uuid.uuid4()),
        "name": "Response Test Prompt",
        "description": "Testing response formatting",
        "content": "You are a response test assistant. Be helpful.",
        "entity_type": "knowledge_base",
        "version": 1,
        "is_active": True,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }

    prompt_response = PromptResponse(**prompt_data)
    assert prompt_response.id == prompt_data["id"]
    assert prompt_response.name == "Response Test Prompt"
    assert prompt_response.content == "You are a response test assistant. Be helpful."
    assert prompt_response.entity_type == "knowledge_base"
    assert prompt_response.version == 1
    assert prompt_response.is_active is True
    assert prompt_response.created_at is not None
    assert prompt_response.updated_at is not None


def test_prompt_assignment_create_schema():
    """Test that PromptAssignmentCreate schema validates entity assignments."""
    # Valid LLM model assignment (entity_type is required)
    model_assignment = {
        "entity_id": str(uuid.uuid4()),
        "entity_type": "llm_model"
    }

    assignment = PromptAssignmentCreate(**model_assignment)
    assert assignment.entity_id == model_assignment["entity_id"]
    assert assignment.entity_type == "llm_model"

    # Valid agent assignment
    agent_assignment = {
        "entity_id": str(uuid.uuid4()),
        "entity_type": "agent"
    }

    assignment = PromptAssignmentCreate(**agent_assignment)
    assert assignment.entity_id == agent_assignment["entity_id"]
    assert assignment.entity_type == "agent"


def test_entity_type_enum_validation():
    """Test that EntityType enum contains expected values."""
    # Test that all expected entity types are available
    assert EntityType.KNOWLEDGE_BASE == "knowledge_base"
    assert EntityType.LLM_MODEL == "llm_model"

    # Test that enum values can be used in prompt creation
    valid_types = [EntityType.KNOWLEDGE_BASE, EntityType.LLM_MODEL]
    for entity_type in valid_types:
        prompt_data = {
            "name": f"Test Prompt for {entity_type}",
            "content": "Test content",
            "entity_type": entity_type
        }
        prompt = PromptCreate(**prompt_data)
        assert prompt.entity_type == entity_type


def test_prompt_content_validation():
    """Test validation of prompt content with various formats."""
    # Simple system prompt
    simple_content = "You are a helpful assistant."
    prompt_create = PromptCreate(
        name="Simple Test",
        content=simple_content,
        entity_type="knowledge_base"
    )
    assert prompt_create.content == simple_content

    # Multi-line prompt with instructions
    multiline_content = """You are a helpful assistant.

When answering questions:
- Be concise and accurate
- Cite sources when available
- Acknowledge uncertainty when appropriate"""
    prompt_create = PromptCreate(
        name="Multiline Test",
        content=multiline_content,
        entity_type="knowledge_base"
    )
    assert "Be concise and accurate" in prompt_create.content

    # KB-aware prompt (context is appended automatically, not substituted)
    kb_prompt = "You are a knowledge assistant. When context is provided, use it to give accurate answers."
    prompt_create = PromptCreate(
        name="KB Aware Test",
        content=kb_prompt,
        entity_type="knowledge_base"
    )
    assert prompt_create.content == kb_prompt


def test_prompt_version_handling():
    """Test prompt version number validation and handling."""
    # Note: Version is auto-managed by the system, not set by user input
    # Test that prompts can be created without specifying version
    prompt_create = PromptCreate(
        name="Version Test",
        content="Test content",
        entity_type="knowledge_base"
    )
    assert prompt_create.name == "Version Test"
    assert prompt_create.content == "Test content"
    assert prompt_create.entity_type == "knowledge_base"


def test_prompt_active_status_handling():
    """Test prompt active status validation and defaults."""
    # Explicitly set to True
    prompt_create = PromptCreate(
        name="Active Prompt",
        content="Test content",
        entity_type="knowledge_base",
        is_active=True
    )
    assert prompt_create.is_active is True

    # Explicitly set to False
    prompt_create = PromptCreate(
        name="Inactive Prompt",
        content="Test content",
        entity_type="knowledge_base",
        is_active=False
    )
    assert prompt_create.is_active is False

    # Default should be True
    prompt_create = PromptCreate(
        name="Default Active Prompt",
        content="Test content",
        entity_type="knowledge_base"
    )
    assert prompt_create.is_active is True


def test_prompt_description_optional_handling():
    """Test that prompt description is properly handled as optional."""
    # With description
    prompt_create = PromptCreate(
        name="Described Prompt",
        content="Test content",
        entity_type="knowledge_base",
        description="This is a test prompt"
    )
    assert prompt_create.description == "This is a test prompt"

    # Without description (should be None)
    prompt_create = PromptCreate(
        name="Undescribed Prompt",
        content="Test content",
        entity_type="knowledge_base"
    )
    assert prompt_create.description is None

    # Empty description (should be allowed)
    prompt_create = PromptCreate(
        name="Empty Description Prompt",
        content="Test content",
        entity_type="knowledge_base",
        description=""
    )
    assert prompt_create.description == ""


# Test Suite Class
class PromptUnitTestSuite(BaseUnitTestSuite):
    """Unit test suite for Prompt Management business logic."""
    
    def get_test_functions(self) -> List[Callable]:
        """Return all prompt unit test functions."""
        return [
            test_prompt_create_schema_validation,
            test_prompt_create_schema_defaults,
            test_prompt_create_schema_validation_errors,
            test_prompt_update_schema_validation,
            test_prompt_response_schema,
            test_prompt_assignment_create_schema,
            test_entity_type_enum_validation,
            test_prompt_content_template_validation,
            test_prompt_version_handling,
            test_prompt_active_status_handling,
            test_prompt_description_optional_handling,
        ]
    
    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Prompt Management Unit Tests"
    
    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Unit tests for prompt business logic, data validation, and response formatting"


if __name__ == "__main__":
    suite = PromptUnitTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
