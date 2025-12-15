from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from shu.models.llm_provider import Message
from shu.models.attachment import Attachment


@dataclass
class ChatMessage:
    """Lightweight chat message DTO used for context building and provider conversion."""

    id: Optional[str]
    role: str
    content: Union[str, List[Dict[str, Any]], None]
    created_at: Optional[Any]
    attachments: List[Attachment]
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def from_message(cls, message: Message, attachments: Optional[List[Attachment]] = None) -> "ChatMessage":
        return cls(
            id=getattr(message, "id", None),
            role=getattr(message, "role", ""),
            content=getattr(message, "content", ""),
            created_at=getattr(message, "created_at", None),
            attachments=list(attachments or []),
            metadata=getattr(message, "message_metadata", None),
        )


@dataclass
class ChatContext:
    """Container for system prompt(s) and chat messages destined for providers."""

    system_prompt: Optional[str]
    messages: List[ChatMessage]

    @classmethod
    def from_dicts(cls, messages: List[Dict[str, Any]], system_prompt: Optional[str] = None) -> "ChatContext":
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
