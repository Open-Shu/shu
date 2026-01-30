"""SQLAlchemy model for Provider Type Definitions (adapter metadata only).

This table now stores minimal metadata used to select a provider adapter.
Per-provider behavior lives in code; secrets remain on LLMProvider.
"""

from sqlalchemy import Boolean, Column, String
from sqlalchemy.orm import relationship

from .base import BaseModel


class ProviderTypeDefinition(BaseModel):
    __tablename__ = "llm_provider_type_definitions"

    # Unique key identifying this provider type (e.g., "openai", "anthropic", "gemini")
    key = Column(String(50), nullable=False, unique=True, index=True)

    # Human-friendly name
    display_name = Column(String(100), nullable=False)

    # Adapter class name to load
    provider_adapter_name = Column(String(100), nullable=False)

    # Optional: allow toggling availability without code removal
    is_active = Column(Boolean, nullable=False, default=True)

    providers = relationship("LLMProvider", back_populates="provider_definition", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"<ProviderTypeDefinition key={self.key} name={self.display_name} "
            f"adapter={self.provider_adapter_name} active={self.is_active}>"
        )
