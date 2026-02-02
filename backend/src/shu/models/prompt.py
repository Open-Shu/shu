"""Generalized Prompt System Models for Shu.

This module implements a unified prompt management system that supports
multiple entity types (knowledge bases, LLM models, agents, etc.) through
a flexible, reusable architecture.

Design Decision:
- Single prompts table with entity_type field for extensibility
- Many-to-many relationship through prompt_assignments table
- Separation of concerns: different prompt types serve different purposes
- Reusability: prompts can be shared across multiple entities of same type
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base
from .base import BaseModel, UUIDMixin


class Prompt(BaseModel):
    """Generalized prompt model supporting multiple entity types.

    This model replaces the previous KnowledgeBasePrompt model and provides
    a unified system for managing prompts across different Shu components.

    Entity Types:
    - 'knowledge_base': RAG prompts for incorporating retrieved context
    - 'llm_model': System prompts for model personality/behavior
    - 'agent': Role-specific instructions for AI agents (future)
    - 'workflow': Workflow-specific prompt templates (future)
    - 'plugin': Plugin-specific prompt instructions (future)
    """

    __tablename__ = "prompts"

    # Core prompt information
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    content = Column(Text, nullable=False)

    # Entity type classification
    entity_type = Column(String(50), nullable=False, index=True)

    # Configuration and metadata
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_system_default = Column(Boolean, default=False, nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)

    # Timestamps are inherited from BaseModel (TimestampMixin)

    # Relationships
    assignments = relationship("PromptAssignment", back_populates="prompt", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Prompt(id={self.id}, name='{self.name}', entity_type='{self.entity_type}')>"

    def activate(self) -> None:
        """Activate this prompt."""
        self.is_active = True

    def deactivate(self) -> None:
        """Deactivate this prompt."""
        self.is_active = False

    def increment_version(self) -> None:
        """Increment the version number."""
        self.version += 1

    @property
    def assigned_entities(self) -> list[str]:
        """Get list of entity IDs this prompt is assigned to."""
        return [assignment.entity_id for assignment in self.assignments]


class PromptAssignment(Base, UUIDMixin):
    """Many-to-many relationship table linking prompts to entities.

    This table enables prompt reusability across multiple entities
    of the same type (e.g., one RAG prompt used by multiple knowledge bases).
    """

    __tablename__ = "prompt_assignments"

    # Foreign keys
    prompt_id = Column(String, ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id = Column(String, nullable=False, index=True)  # ID of the entity (KB, model, agent, etc.)

    # Assignment metadata
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    assigned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    prompt = relationship("Prompt", back_populates="assignments")

    def __repr__(self) -> str:
        return f"<PromptAssignment(prompt_id={self.prompt_id}, entity_id={self.entity_id})>"

    def activate(self) -> None:
        """Activate this assignment."""
        self.is_active = True

    def deactivate(self) -> None:
        """Deactivate this assignment."""
        self.is_active = False


# Database indexes for performance
Index("idx_prompts_entity_type_active", Prompt.entity_type, Prompt.is_active)
Index("idx_prompts_name_entity_type", Prompt.name, Prompt.entity_type)
Index("idx_prompt_assignments_entity_active", PromptAssignment.entity_id, PromptAssignment.is_active)
Index("idx_prompt_assignments_prompt_entity", PromptAssignment.prompt_id, PromptAssignment.entity_id)


# Entity type constants for type safety
class EntityType:
    """Constants for supported entity types."""

    KNOWLEDGE_BASE = "knowledge_base"  # For KB context prompts (assigned via model configs)
    LLM_MODEL = "llm_model"
    MODEL_CONFIGURATION = "model_configuration"
    AGENT = "agent"
    WORKFLOW = "workflow"
    PLUGIN = "plugin"

    @classmethod
    def all(cls) -> list[str]:
        """Get all supported entity types."""
        return [
            cls.KNOWLEDGE_BASE,
            cls.LLM_MODEL,
            cls.MODEL_CONFIGURATION,
            cls.AGENT,
            cls.WORKFLOW,
            cls.PLUGIN,
        ]

    @classmethod
    def validate(cls, entity_type: str) -> bool:
        """Validate if entity type is supported."""
        return entity_type in cls.all()


# Default prompts for LLM models
DEFAULT_LLM_MODEL_PROMPTS = {
    "helpful_assistant": {
        "name": "Helpful Assistant",
        "description": "A friendly, helpful AI assistant",
        "content": "You are a helpful, harmless, and honest AI assistant. Provide clear, accurate, and useful responses to user queries.",
        "entity_type": EntityType.LLM_MODEL,
    },
    "technical_expert": {
        "name": "Technical Expert",
        "description": "An AI assistant specializing in technical topics",
        "content": "You are a technical expert AI assistant. Provide detailed, accurate technical information with examples and best practices when appropriate.",
        "entity_type": EntityType.LLM_MODEL,
    },
    "research_analyst": {
        "name": "Research Analyst",
        "description": "An AI assistant focused on research and analysis",
        "content": "You are a research analyst AI assistant. Provide thorough analysis, cite sources when available, and present information in a structured, analytical manner.",
        "entity_type": EntityType.LLM_MODEL,
    },
}

# Default prompts for knowledge base contexts (used with model configurations)
# Note: These are system prompts - context from knowledge bases is automatically
# appended as a separate section by the message context builder
DEFAULT_KB_CONTEXT_PROMPTS = {
    "academic_research": {
        "name": "Academic Research Assistant",
        "description": "Provides comprehensive answers with scholarly rigor and citations",
        "content": "You are an academic research assistant. When context from knowledge bases is provided, use it to give comprehensive answers with scholarly rigor. Include relevant citations from the provided sources and maintain academic standards in your response.",
        "entity_type": EntityType.LLM_MODEL,
    },
    "business_analyst": {
        "name": "Business Analyst",
        "description": "Focuses on practical insights and actionable recommendations",
        "content": "You are a business analyst assistant. When context from knowledge bases is provided, use it to give clear and actionable answers. Focus on practical insights and recommendations that can be implemented. Reference source documents when citing specific data or claims.",
        "entity_type": EntityType.LLM_MODEL,
    },
    "technical_documentation": {
        "name": "Technical Documentation Assistant",
        "description": "Provides precise technical answers with code examples",
        "content": "You are a technical documentation assistant. When context from knowledge bases is provided, use it to give precise and accurate answers. Include specific technical details and code examples where relevant. Reference the source documents when citing specific information.",
        "entity_type": EntityType.LLM_MODEL,
    },
    "general_knowledge": {
        "name": "General Knowledge Assistant",
        "description": "Provides helpful answers based on retrieved context",
        "content": "You are a helpful knowledge assistant. When context from knowledge bases is provided, use it to give accurate and informative answers. If the context doesn't contain relevant information, say so rather than speculating.",
        "entity_type": EntityType.LLM_MODEL,
    },
}


# Table constraints and relationships will be handled by Alembic migration:
# - Unique constraint on (name, entity_type) to prevent duplicate prompt names per type
# - Unique constraint on (prompt_id, entity_id) to prevent duplicate assignments
# - Foreign key constraint on prompt_id
# - Indexes for performance optimization
