"""
Pydantic schemas for LLM Side-Call API.
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class SideCallModelResponse(BaseModel):
    """Response schema for side-call model information."""

    id: str = Field(..., description="Model configuration ID")
    name: str = Field(..., description="Model configuration name")
    description: Optional[str] = Field(
        None, description="Model configuration description"
    )
    provider_name: Optional[str] = Field(None, description="LLM provider name")
    model_name: str = Field(..., description="LLM model name")
    functionalities: Dict[str, Any] = Field(
        default_factory=dict, description="Model functionalities"
    )


class SideCallConfigRequest(BaseModel):
    """Request schema for setting side-call configuration."""

    model_config_id: str = Field(
        ..., description="ID of the model configuration to designate for side-calls"
    )


class SideCallConfigResponse(BaseModel):
    """Response schema for side-call configuration."""

    configured: bool = Field(..., description="Whether a side-call model is configured")
    side_call_model_config: Optional[SideCallModelResponse] = Field(
        None, description="The configured side-call model"
    )
    message: str = Field(..., description="Status message")


class ConversationAutomationRequest(BaseModel):
    """Request payload for conversation automation."""

    timeout_ms: Optional[int] = Field(
        None,
        description="Optional timeout override in milliseconds for the side-call execution",
    )
    fallback_user_message: Optional[str] = Field(
        None,
        description="Optional plaintext fallback of the most recent user message when conversation history is not yet persisted",
    )


class ConversationSummaryPayload(BaseModel):
    """Response payload when generating or refreshing a summary."""

    summary: str = Field(..., description="Current persisted summary for the conversation")
    last_message_id: Optional[str] = Field(
        None,
        description="Identifier of the most recent message incorporated into the summary",
    )
    was_updated: bool = Field(
        ..., description="Indicates whether a new side-call was executed and the summary changed"
    )
    tokens_used: int = Field(0, description="Number of tokens consumed by the side-call")
    response_time_ms: Optional[int] = Field(None, description="Side-call response time in milliseconds")
    model_config_id: Optional[str] = Field(
        None, description="Identifier of the side-call model configuration used for the summary"
    )


class ConversationRenamePayload(BaseModel):
    """Response payload when auto-renaming a conversation."""

    title: str = Field(..., description="Resulting conversation title")
    was_renamed: bool = Field(..., description="Indicates whether the title was changed in this request")
    title_locked: bool = Field(..., description="Flag denoting whether manual rename prevents auto-rename")
    reason: Optional[str] = Field(
        None, description="Optional explanation when auto-rename is skipped (e.g., locked, no new messages)"
    )
    last_message_id: Optional[str] = Field(
        None, description="Identifier of the conversation message snapshot used for rename heuristics"
    )
    tokens_used: int = Field(0, description="Number of tokens consumed by the side-call")
    response_time_ms: Optional[int] = Field(None, description="Side-call response time in milliseconds")
    model_config_id: Optional[str] = Field(
        None, description="Identifier of the side-call model configuration used for rename generation"
    )


class AutoRenameLockStatus(BaseModel):
    """Minimal payload for reporting auto-rename lock state."""

    title_locked: bool = Field(..., description="Whether auto-rename is locked due to manual rename")
