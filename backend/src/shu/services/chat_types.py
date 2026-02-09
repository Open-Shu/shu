from dataclasses import dataclass
from typing import Any

from shu.models.attachment import Attachment
from shu.models.llm_provider import Message


@dataclass
class ChatMessage:
    """Lightweight chat message DTO used for context building and provider conversion."""

    id: str | None
    role: str
    content: str | list[dict[str, Any]] | None
    created_at: Any | None
    attachments: list[Attachment]
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_message(cls, message: Message, attachments: list[Attachment] | None = None) -> "ChatMessage":
        return cls(
            id=getattr(message, "id", None),
            role=getattr(message, "role", ""),
            content=getattr(message, "content", ""),
            created_at=getattr(message, "created_at", None),
            attachments=list(attachments or []),
            metadata=getattr(message, "message_metadata", None),
        )

    @classmethod
    def build(
        cls,
        role: str,
        content: str | list[dict[str, Any]] | None,
        *,
        id: str | None = None,
        created_at: Any | None = None,
        attachments: list[Attachment] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ChatMessage":
        """Create a ChatMessage object with sensible defaults."""
        return cls(
            id=id,
            role=role,
            content=content,
            created_at=created_at,
            attachments=list(attachments or []),
            metadata=metadata,
        )


@dataclass
class ChatContext:
    """Container for system prompt(s) and chat messages destined for providers."""

    system_prompt: str | None
    messages: list[ChatMessage]

    @classmethod
    def from_dicts(cls, messages: list[dict[str, Any]], system_prompt: str | None = None) -> "ChatContext":
        """Build ChatContext from a list of simple role/content dicts."""
        chat_messages = []
        for m in messages:
            chat_messages.append(
                ChatMessage(
                    id=m.get("id"),
                    role=m.get("role", ""),
                    content=m.get("content", ""),
                    created_at=m.get("created_at"),
                    attachments=m.get("attachments") or [],
                    metadata=m.get("metadata") or m.get("message_metadata"),
                )
            )
        return cls(system_prompt=system_prompt, messages=chat_messages)
