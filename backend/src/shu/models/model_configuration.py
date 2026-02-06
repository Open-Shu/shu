"""Model Configuration models for Shu.

This module defines the ModelConfiguration entity - the foundational abstraction
that combines base models + prompts + optional knowledge bases into user-facing
configurations that users select for chat and other interactions.

Design Decision:
- ModelConfiguration = Base Model + Prompt + Optional Knowledge Bases
- Users select "Research Assistant" instead of "GPT-4 + research prompt + biology KB"
- This is the atomic unit that chat conversations and other features use
"""

from typing import Optional

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Index, String, Table, Text
from sqlalchemy.orm import relationship

from .base import BaseModel

# Many-to-many association table for ModelConfiguration <-> KnowledgeBase
model_configuration_knowledge_bases = Table(
    "model_configuration_knowledge_bases",
    BaseModel.metadata,
    Column(
        "model_configuration_id",
        String,
        ForeignKey("model_configurations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "knowledge_base_id",
        String,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class ModelConfiguration(BaseModel):
    """Model Configuration entity - the user-facing abstraction for AI interactions.

    This combines:
    - Base Model (LLMProvider + specific model name)
    - Prompt (system instructions and behavior)
    - Optional Knowledge Bases (for RAG integration)

    Examples:
    - "Research Assistant": GPT-4 + Research Prompt + [Biology KB, Chemistry KB]
    - "Customer Support": Claude-3 + Support Prompt + [FAQ KB, Product KB]
    - "General Chat": GPT-4 + Friendly Prompt + [] (no KBs)

    """

    __tablename__ = "model_configurations"

    # Basic configuration information
    name = Column(String(100), nullable=False, index=True)  # "Research Assistant", "Customer Support"
    description = Column(Text, nullable=True)  # User-friendly description

    # Base model configuration
    llm_provider_id = Column(String, ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name = Column(String(100), nullable=False)  # Specific model (gpt-4, claude-3-opus)

    # Prompt configuration
    prompt_id = Column(String, ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True, index=True)

    # Status and ownership
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_by = Column(String, nullable=False, index=True)  # User ID who created this configuration

    # Per-model LLM parameter overrides (admin-controlled)
    parameter_overrides = Column(JSON, nullable=True)

    # Relationships
    llm_provider = relationship("LLMProvider", back_populates="model_configurations")
    prompt = relationship("Prompt")
    knowledge_bases = relationship(
        "KnowledgeBase",
        secondary=model_configuration_knowledge_bases,
        back_populates="model_configurations",
    )
    conversations = relationship("Conversation", back_populates="model_configuration")
    kb_prompt_assignments = relationship(
        "ModelConfigurationKBPrompt",
        back_populates="model_configuration",
        cascade="all, delete-orphan",
    )

    functionalities = Column(JSON, nullable=True)

    def __repr__(self) -> str:
        """Represent as string."""
        return f"<ModelConfiguration(id={self.id}, name='{self.name}', provider='{self.llm_provider_id}')>"

    @property
    def has_knowledge_bases(self) -> bool:
        """Check if this configuration has attached knowledge bases."""
        return len(self.knowledge_bases) > 0

    @property
    def has_kb_prompts(self) -> bool:
        """Check if this configuration has any KB-specific prompts assigned."""
        return len([assignment for assignment in self.kb_prompt_assignments if assignment.is_active]) > 0

    def get_kb_prompt(self, knowledge_base_id: str) -> Optional["Prompt"]:  # noqa: F821 # indirect typing is fine
        """Get the prompt assigned to a specific knowledge base for this model configuration.

        Args:
            knowledge_base_id: ID of the knowledge base

        Returns:
            Prompt object if assigned, None otherwise

        """
        for assignment in self.kb_prompt_assignments:
            if assignment.knowledge_base_id == knowledge_base_id and assignment.is_active:
                return assignment.prompt
        return None

    def get_all_kb_prompts(self) -> dict:
        """Get all KB-specific prompts for this model configuration.

        Returns:
            Dictionary mapping knowledge_base_id to Prompt object

        """
        kb_prompts = {}
        for assignment in self.kb_prompt_assignments:
            if assignment.is_active:
                kb_prompts[assignment.knowledge_base_id] = assignment.prompt
        return kb_prompts

    @property
    def knowledge_base_ids(self) -> list[str]:
        """Get list of knowledge base IDs attached to this configuration."""
        return [kb.id for kb in self.knowledge_bases]

    def activate(self) -> None:
        """Activate this model configuration."""
        self.is_active = True

    def deactivate(self) -> None:
        """Deactivate this model configuration."""
        self.is_active = False


# Database indexes for performance
Index("idx_model_configurations_active", ModelConfiguration.is_active)
Index("idx_model_configurations_provider", ModelConfiguration.llm_provider_id)
Index("idx_model_configurations_created_by", ModelConfiguration.created_by)
Index("idx_model_configurations_name_active", ModelConfiguration.name, ModelConfiguration.is_active)
