"""Model Configuration KB Prompt models for Shu.

This module defines the association between model configurations and
knowledge base-specific prompts, enabling different prompts for the
same KB across different model configurations.

Design Decision:
- KB prompts are now part of model configuration, not KB configuration
- Same KB can have different prompts for different model configurations
- Enables better reusability and separation of concerns
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import BaseModel


class ModelConfigurationKBPrompt(BaseModel):
    """Association between model configurations and KB-specific prompts.

    This table enables model configurations to have different prompts
    for each knowledge base they use, providing maximum flexibility
    for RAG interactions.

    Examples:
    - "Research Assistant" config uses formal academic prompt for Biology KB
    - "Student Helper" config uses casual explanatory prompt for same Biology KB
    - "Legal Research" config uses citation-heavy prompt for Legal KB
    - "Legal Summary" config uses plain-language prompt for same Legal KB

    """

    __tablename__ = "model_configuration_kb_prompts"

    # Foreign keys
    model_configuration_id = Column(
        String,
        ForeignKey("model_configurations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id = Column(String, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt_id = Column(String, ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False, index=True)

    # Assignment metadata
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    assigned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    model_configuration = relationship("ModelConfiguration", back_populates="kb_prompt_assignments")
    knowledge_base = relationship("KnowledgeBase")
    prompt = relationship("Prompt")

    # Constraints
    __table_args__ = (
        # Ensure one prompt per KB per model configuration
        UniqueConstraint("model_configuration_id", "knowledge_base_id", name="uq_model_config_kb_prompt"),
        # Index for efficient lookups
        Index("ix_model_config_kb_prompts_lookup", "model_configuration_id", "knowledge_base_id"),
        Index("ix_model_config_kb_prompts_active", "is_active", "model_configuration_id"),
    )

    def __repr__(self) -> str:
        return f"<ModelConfigurationKBPrompt(model_config={self.model_configuration_id}, kb={self.knowledge_base_id}, prompt={self.prompt_id})>"

    def activate(self):
        """Activate this KB prompt assignment."""
        self.is_active = True

    def deactivate(self):
        """Deactivate this KB prompt assignment."""
        self.is_active = False
