"""Chat-related Pydantic schemas for request/response validation.

This module contains schemas for chat conversations, messages, and related operations.
"""

from typing import ClassVar

from pydantic import BaseModel, Field


class ConversationFromExperienceRequest(BaseModel):
    """Schema for creating a conversation from an experience run.

    This schema allows users to optionally override the conversation title
    when creating a conversation from an experience result. If no title is
    provided, the conversation will use the experience name as the title.
    """

    title: str | None = Field(
        None,
        description="Optional custom title for the conversation (defaults to experience name if not provided)",
    )

    class Config:
        """Pydantic model configuration."""

        json_schema_extra: ClassVar[dict[str, dict]] = {"example": {"title": "Follow-up on Morning Briefing"}}
