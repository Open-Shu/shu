"""
Model Configuration schemas for Shu.

This module defines Pydantic schemas for the ModelConfiguration entity,
which is the foundational abstraction that combines base models + prompts +
optional knowledge bases into user-facing configurations.
"""

from pydantic import BaseModel, Field, validator, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime

# Note: Related entity responses are defined inline to avoid circular imports

# KB Prompt Assignment Schema (defined early to avoid forward references)
class ModelConfigKBPromptAssignment(BaseModel):
    """Schema for assigning a prompt to a KB in a model configuration."""

    knowledge_base_id: str = Field(..., description="Knowledge base ID")
    prompt_id: str = Field(..., description="Prompt ID to assign")


class ModelConfigurationBase(BaseModel):
    """Base schema for model configuration with common fields."""

    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(..., min_length=1, max_length=100, description="Model configuration name")
    description: Optional[str] = Field(None, description="User-friendly description")
    llm_provider_id: str = Field(..., description="LLM provider ID")
    model_name: str = Field(..., min_length=1, max_length=100, description="Specific model name")
    prompt_id: Optional[str] = Field(None, description="Associated prompt ID")
    is_active: bool = Field(True, description="Whether configuration is active")

    @validator('name')
    def validate_name(cls, v):
        """Validate configuration name."""
        if not v.strip():
            raise ValueError("Configuration name cannot be empty")
        return v.strip()

    @validator('model_name')
    def validate_model_name(cls, v):
        """Validate model name."""
        if not v.strip():
            raise ValueError("Model name cannot be empty")
        return v.strip()

    @validator('prompt_id')
    def validate_prompt_id(cls, v):
        """Validate prompt ID - convert empty string to None."""
        if v == '':
            return None
        return v


class ModelConfigurationCreate(ModelConfigurationBase):
    """Schema for creating model configurations."""

    knowledge_base_ids: List[str] = Field(default_factory=list, description="Knowledge base IDs to attach")
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict, description="Per-model LLM parameter overrides (admin-controlled); validated only for known mapped keys")
    functionalities: Optional[Dict[str, Any]] = Field(None, description="Enabled functionalities for the given model")
    is_side_call_model: bool = Field(False, description="Whether this model is designated for side-calls")

    kb_prompt_assignments: List[ModelConfigKBPromptAssignment] = Field(
        default_factory=list,
        description="KB-specific prompt assignments"
    )
    created_by: str = Field(..., description="User ID who created this configuration")


class ModelConfigurationUpdate(BaseModel):
    """Schema for updating model configurations."""

    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    llm_provider_id: Optional[str] = None
    model_name: Optional[str] = Field(None, min_length=1, max_length=100)
    prompt_id: Optional[str] = None
    parameter_overrides: Optional[Dict[str, Any]] = Field(None, description="Replace per-model LLM parameter overrides JSON (entire object)")

    is_active: Optional[bool] = None
    knowledge_base_ids: Optional[List[str]] = None
    kb_prompt_assignments: Optional[List[ModelConfigKBPromptAssignment]] = Field(
        None,
        description="KB-specific prompt assignments to update"
    )
    functionalities: Optional[Dict[str, Any]] = Field(None, description="Enabled functionalities for the given model")
    is_side_call_model: Optional[bool] = Field(None, description="Whether this model is designated for side-calls")

    @validator('name')
    def validate_name(cls, v):
        """Validate configuration name."""
        if v is not None and not v.strip():
            raise ValueError("Configuration name cannot be empty")
        return v.strip() if v else v

    @validator('model_name')
    def validate_model_name(cls, v):
        """Validate model name."""
        if v is not None and not v.strip():
            raise ValueError("Model name cannot be empty")
        return v.strip() if v else v

    @validator('prompt_id')
    def validate_prompt_id(cls, v):
        """Validate prompt ID - convert empty string to None."""
        if v == '':
            return None
        return v


class ModelConfigurationResponse(ModelConfigurationBase):
    """Schema for model configuration responses."""

    id: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Expanded relationships (optional - populated by service layer)
    llm_provider: Optional[dict] = None
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict, description="Per-model LLM parameter overrides")

    prompt: Optional[dict] = None
    knowledge_bases: List[dict] = Field(default_factory=list)
    kb_prompts: Dict[str, dict] = Field(default_factory=dict, description="KB-specific prompts mapped by KB ID")

    # Computed properties
    has_knowledge_bases: bool = Field(..., description="Whether configuration has attached KBs")
    knowledge_base_count: int = Field(..., description="Number of attached knowledge bases")

    model_config = ConfigDict(from_attributes=True)

    functionalities: Dict[str, Any] = Field(default_factory=dict, description="Enabled functionalities for this model")
    is_side_call: bool = Field(False, description="Whether this model is designated for side-calls")


class ModelConfigurationList(BaseModel):
    """Schema for paginated model configuration lists."""

    items: List[ModelConfigurationResponse]
    total: int
    page: int = 1
    per_page: int = 50
    pages: int

    model_config = ConfigDict(from_attributes=True)


class ModelConfigurationTest(BaseModel):
    """Schema for testing model configurations."""

    test_message: str = Field(..., min_length=1, description="Test message to send")
    include_knowledge_bases: bool = Field(True, description="Whether to include KB context")

    @validator('test_message')
    def validate_test_message(cls, v):
        """Validate test message."""
        if not v.strip():
            raise ValueError("Test message cannot be empty")
        return v.strip()


class ModelConfigurationTestResponse(BaseModel):
    """Schema for model configuration test responses."""

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)

    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

    # Test details
    model_used: str
    prompt_applied: bool
    knowledge_bases_used: List[str] = Field(default_factory=list)
    response_time_ms: Optional[int] = None
    token_usage: Optional[dict] = None


# KB Prompt Assignment Response Schemas

class ModelConfigKBPromptResponse(BaseModel):
    """Schema for KB prompt assignment responses."""

    id: str
    model_configuration_id: str
    knowledge_base_id: str
    prompt_id: str
    is_active: bool
    assigned_at: datetime
    created_at: datetime
    updated_at: datetime

    # Expanded relationships (optional)
    knowledge_base: Optional[dict] = None
    prompt: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class ModelConfigKBPromptList(BaseModel):
    """Schema for listing KB prompt assignments."""

    assignments: List[ModelConfigKBPromptResponse]
    total: int
    model_configuration_id: str
