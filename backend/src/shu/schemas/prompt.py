"""Pydantic schemas for the generalized prompt system.

This module provides request/response schemas for the unified prompt
management system that supports multiple entity types.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..models.prompt import EntityType


class EntityTypeEnum(str, Enum):
    """Enum for supported entity types."""

    KNOWLEDGE_BASE = EntityType.KNOWLEDGE_BASE  # For KB context prompts (assigned via model configs)
    LLM_MODEL = EntityType.LLM_MODEL
    MODEL_CONFIGURATION = EntityType.MODEL_CONFIGURATION
    AGENT = EntityType.AGENT
    WORKFLOW = EntityType.WORKFLOW
    PLUGIN = EntityType.PLUGIN


# Base schemas
class PromptBase(BaseModel):
    """Base schema for prompt data."""

    name: str = Field(..., min_length=1, max_length=255, description="Prompt name")
    description: str | None = Field(None, description="Optional prompt description")
    content: str = Field(..., min_length=1, description="Prompt content/template")
    entity_type: EntityTypeEnum = Field(..., description="Type of entity this prompt is for")
    is_active: bool = Field(True, description="Whether the prompt is active")
    is_system_default: bool = Field(False, description="Whether this is a system default prompt (uneditable)")


class PromptCreate(PromptBase):
    """Schema for creating a new prompt."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate prompt name."""
        if not v or not v.strip():
            raise ValueError("Prompt name cannot be empty")
        return v.strip()

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate prompt content."""
        if not v or not v.strip():
            raise ValueError("Prompt content cannot be empty")
        return v.strip()


class PromptUpdate(BaseModel):
    """Schema for updating an existing prompt."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    content: str | None = Field(None, min_length=1)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate prompt name if provided."""
        if v is not None and (not v or not v.strip()):
            raise ValueError("Prompt name cannot be empty")
        return v.strip() if v else v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate prompt content if provided."""
        if v is not None and (not v or not v.strip()):
            raise ValueError("Prompt content cannot be empty")
        return v.strip() if v else v


class PromptAssignmentBase(BaseModel):
    """Base schema for prompt assignments."""

    entity_id: str = Field(..., description="ID of the entity to assign prompt to")
    is_active: bool = Field(True, description="Whether the assignment is active")


class PromptAssignmentCreate(PromptAssignmentBase):
    """Schema for creating a prompt assignment."""

    entity_type: EntityTypeEnum = Field(..., description="Type of entity to assign prompt to")


class PromptAssignmentUpdate(BaseModel):
    """Schema for updating a prompt assignment."""

    is_active: bool | None = None


# Response schemas
class PromptAssignmentResponse(PromptAssignmentBase):
    """Schema for prompt assignment responses."""

    id: str
    prompt_id: str
    assigned_at: datetime

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class PromptResponse(PromptBase):
    """Schema for prompt responses."""

    id: str
    version: int
    created_at: datetime
    updated_at: datetime
    assignments: list[PromptAssignmentResponse] = Field(default_factory=list)

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True

    @property
    def assigned_entity_ids(self) -> list[str]:
        """Get list of entity IDs this prompt is assigned to."""
        return [assignment.entity_id for assignment in self.assignments if assignment.is_active]


class PromptListResponse(BaseModel):
    """Schema for prompt list responses."""

    items: list[PromptResponse]
    total: int
    entity_type: EntityTypeEnum | None = None


# Template and default schemas
class PromptTemplate(BaseModel):
    """Schema for prompt templates."""

    name: str
    description: str
    content: str
    entity_type: EntityTypeEnum


class PromptTemplateList(BaseModel):
    """Schema for prompt template lists."""

    templates: list[PromptTemplate]
    entity_type: EntityTypeEnum | None = None


# Entity-specific schemas for convenience
class KnowledgeBasePromptCreate(PromptCreate):
    """Convenience schema for creating knowledge base context prompts."""

    entity_type: Literal[EntityTypeEnum.KNOWLEDGE_BASE] = Field(EntityTypeEnum.KNOWLEDGE_BASE)


class LLMModelPromptCreate(PromptCreate):
    """Convenience schema for creating LLM model prompts."""

    entity_type: Literal[EntityTypeEnum.LLM_MODEL] = Field(EntityTypeEnum.LLM_MODEL)


class AgentPromptCreate(PromptCreate):
    """Convenience schema for creating agent prompts."""

    entity_type: Literal[EntityTypeEnum.AGENT] = Field(EntityTypeEnum.AGENT)


# Query and filter schemas
class PromptQueryParams(BaseModel):
    """Schema for prompt query parameters."""

    entity_type: EntityTypeEnum | None = None
    entity_id: str | None = None
    is_active: bool | None = None
    search: str | None = Field(None, description="Search in name and description")
    limit: int = Field(50, ge=1, le=100, description="Maximum number of results")
    offset: int = Field(0, ge=0, description="Number of results to skip")


# Statistics and analytics schemas
class PromptUsageStats(BaseModel):
    """Schema for prompt usage statistics."""

    prompt_id: str
    prompt_name: str
    entity_type: EntityTypeEnum
    assignment_count: int
    active_assignment_count: int
    last_used: datetime | None = None


class PromptSystemStats(BaseModel):
    """Schema for overall prompt system statistics."""

    total_prompts: int
    active_prompts: int
    total_assignments: int
    active_assignments: int
    prompts_by_entity_type: dict[str, int]
    assignments_by_entity_type: dict[str, int]


# Migration and compatibility schemas
class LegacyKnowledgeBasePromptMigration(BaseModel):
    """Schema for migrating legacy knowledge base prompts."""

    knowledge_base_id: str
    legacy_prompts: list[dict[str, Any]]


class MigrationResult(BaseModel):
    """Schema for migration operation results."""

    success: bool
    migrated_count: int
    failed_count: int
    errors: list[str] = Field(default_factory=list)
    created_prompt_ids: list[str] = Field(default_factory=list)
