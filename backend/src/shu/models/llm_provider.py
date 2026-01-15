"""
LLM Provider database models for Shu RAG Backend.

This module defines the database models for managing LLM providers,
models, and usage tracking in the Shu system.
"""

from sqlalchemy import Column, String, Boolean, Integer, Text, ForeignKey, DECIMAL, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from typing import Optional, Dict, Any, List
import uuid

from .base import BaseModel


class LLMProvider(BaseModel):
    """Database model for LLM providers."""
    
    __tablename__ = "llm_providers"
    
    # Basic provider information
    name = Column(String(100), nullable=False, index=True)  # "OpenAI Production", "Anthropic Research"
    provider_type = Column(
        String(50),
        ForeignKey("llm_provider_type_definitions.key", ondelete="CASCADE"),
        nullable=False,
        index=True
    )  # "openai", "anthropic", "ollama", "azure"
    api_key_encrypted = Column(Text, nullable=True)  # Encrypted API key
    organization_id = Column(String(100), nullable=True)  # Optional org/project ID
    
    # Provider capabilities
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Rate limiting (per-provider limits, 0 = disabled)
    rate_limit_rpm = Column(Integer, default=60, nullable=False)  # Requests per minute
    rate_limit_tpm = Column(Integer, default=60000, nullable=False)  # Tokens per minute
    budget_limit_monthly = Column(DECIMAL(10, 2), nullable=True)  # Monthly budget limit
    
    # Additional configuration
    config = Column(JSON, nullable=True)  # Provider-specific configuration
    
    # Relationships
    models = relationship("LLMModel", back_populates="provider", cascade="all, delete-orphan")
    usage_records = relationship("LLMUsage", back_populates="provider", cascade="all, delete")
    model_configurations = relationship("ModelConfiguration", back_populates="llm_provider", cascade="all, delete-orphan")
    provider_definition = relationship("ProviderTypeDefinition", back_populates="providers")

    def __init__(
        self,
        *args,
        api_endpoint: Optional[str] = None,
        supports_streaming: Optional[bool] = None,
        supports_functions: Optional[bool] = None,
        supports_vision: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(*args, config=config, **kwargs)

        cfg = self._config_dict()
        updated = False

        if api_endpoint is not None and not cfg.get("get_api_base_url"):
            cfg["get_api_base_url"] = api_endpoint
            updated = True

        capabilities = cfg.get("get_capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}

        def _set_capability(key: str, value: Optional[bool], label: str):
            nonlocal updated, capabilities
            if value is None:
                return
            capabilities[key] = {"value": bool(value), "label": label}
            updated = True

        _set_capability("streaming", supports_streaming, "Supports Streaming")
        _set_capability("tools", supports_functions, "Supports Tool Calling")
        _set_capability("vision", supports_vision, "Supports Vision")

        if capabilities:
            cfg["get_capabilities"] = capabilities
            updated = True

        if updated:
            self.config = cfg

    def _config_dict(self) -> Dict[str, Any]:
        return dict(self.config) if isinstance(self.config, dict) else {}

    @property
    def api_endpoint(self) -> Optional[str]:
        value = self._config_dict().get("get_api_base_url")
        return value if isinstance(value, str) else None

    def _capability_value(self, capability: str) -> bool:
        caps = self._config_dict().get("get_capabilities") or {}
        if not isinstance(caps, dict):
            return False
        entry = caps.get(capability) or {}
        return bool(entry.get("value"))

    @property
    def supports_streaming(self) -> bool:
        return self._capability_value("streaming")

    @property
    def supports_functions(self) -> bool:
        return self._capability_value("tools")

    @property
    def supports_vision(self) -> bool:
        return self._capability_value("vision")

    def __repr__(self) -> str:
        return f"<LLMProvider(name='{self.name}', type='{self.provider_type}', active={self.is_active})>"


class LLMModel(BaseModel):
    """Database model for available LLM models."""
    
    __tablename__ = "llm_models"
    
    # Provider relationship
    provider_id = Column(String, ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Model information
    model_name = Column(String(100), nullable=False, index=True)  # "gpt-4", "claude-3-opus"
    display_name = Column(String(100), nullable=True)  # "GPT-4 (Latest)"
    model_type = Column(String(50), default="chat", nullable=False)  # "chat", "completion", "embedding"
    
    # Model capabilities
    supports_streaming = Column(Boolean, default=True, nullable=False)
    supports_functions = Column(Boolean, default=False, nullable=False)
    supports_vision = Column(Boolean, default=False, nullable=False)

    # Cost information (per-model pricing)
    cost_per_input_token = Column(DECIMAL(12, 10), nullable=True)
    cost_per_output_token = Column(DECIMAL(12, 10), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Additional configuration
    config = Column(JSON, nullable=True)  # Model-specific configuration
    
    # Relationships
    provider = relationship("LLMProvider", back_populates="models")
    usage_records = relationship("LLMUsage", back_populates="model")
    
    def __repr__(self) -> str:
        return f"<LLMModel(name='{self.model_name}', provider='{self.provider.name if self.provider else 'Unknown'}')>"


class LLMUsage(BaseModel):
    """Database model for tracking LLM usage and costs."""
    
    __tablename__ = "llm_usage"
    
    # Provider and model references
    provider_id = Column(String, ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id = Column(String, ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # User reference (from auth system)
    user_id = Column(String, nullable=True, index=True)  # Optional user tracking
    
    # Usage metrics
    request_type = Column(String(50), nullable=False, index=True)  # "chat", "completion", "embedding"
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    
    # Cost tracking
    input_cost = Column(DECIMAL(10, 6), default=0, nullable=False)
    output_cost = Column(DECIMAL(10, 6), default=0, nullable=False)
    total_cost = Column(DECIMAL(10, 6), default=0, nullable=False)
    
    # Performance metrics
    response_time_ms = Column(Integer, nullable=True)  # Response time in milliseconds
    success = Column(Boolean, default=True, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    
    # Request metadata
    request_metadata = Column(JSON, nullable=True)  # Additional request/response metadata
    
    # Relationships
    provider = relationship("LLMProvider", back_populates="usage_records")
    model = relationship("LLMModel", back_populates="usage_records")
    
    def __repr__(self) -> str:
        return f"<LLMUsage(model='{self.model.model_name if self.model else 'Unknown'}', tokens={self.total_tokens}, cost=${self.total_cost})>"


class Conversation(BaseModel):
    """Database model for chat conversations."""
    
    __tablename__ = "conversations"
    
    # User reference
    user_id = Column(String, nullable=False, index=True)  # User who owns the conversation
    
    # Conversation metadata
    title = Column(String(200), nullable=True)  # Optional conversation title

    # Model Configuration (replaces direct provider/model references)
    model_configuration_id = Column(String, ForeignKey("model_configurations.id", ondelete="SET NULL"), nullable=True, index=True)
    meta = Column(JSON, nullable=True)
    summary_text = Column(Text, nullable=True)

    
    # Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_favorite = Column(Boolean, default=False, nullable=False, index=True)  # Favorite status for pinning conversations
    
    # Relationships
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    model_configuration = relationship("ModelConfiguration", back_populates="conversations")
    # Chat attachments (scoped to conversation)
    attachments = relationship("Attachment", back_populates="conversation", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Conversation(title='{self.title}', user_id='{self.user_id}')>"


class Message(BaseModel):
    """Database model for individual messages in conversations."""

    __tablename__ = "messages"

    # Override updated_at from BaseModel since messages table doesn't have it
    updated_at = None
    
    # Conversation reference
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Message content
    role = Column(String(20), nullable=False, index=True)  # "user", "assistant", "system"
    content = Column(Text, nullable=False)
    
    # LLM tracking
    model_id = Column(String, ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Message metadata
    message_metadata = Column(JSON, nullable=True)  # Tool calls, costs, processing time, etc.

    # Variant lineage
    parent_message_id = Column(String, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True)
    variant_index = Column(Integer, nullable=True)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    model = relationship("LLMModel")
    # Attachments linked via association table; lazy selectin to avoid greenlet issues
    attachments = relationship("Attachment", secondary="message_attachments", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Message(role='{self.role}', conversation_id='{self.conversation_id}')>"
