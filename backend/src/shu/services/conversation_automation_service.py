"""Conversation automation helpers for summaries and auto-rename."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.config import ConfigurationManager
from shu.models.llm_provider import Conversation, Message
from shu.services.chat_service import ChatService
from shu.services.message_utils import collapse_assistant_variants
from shu.services.side_call_service import SideCallService

logger = logging.getLogger(__name__)


class ConversationAutomationService:
    """Coordinates conversation summaries and auto-rename flows."""

    def __init__(self, db_session: AsyncSession, config_manager: ConfigurationManager) -> None:
        self.db_session = db_session
        self.config_manager = config_manager
        self.settings = config_manager.settings
        self.chat_service = ChatService(db_session, config_manager)
        self.side_call_service = SideCallService(db_session, config_manager)

    def _get_summary_settings(self) -> dict[str, Any]:
        return {
            "system_prompt": self.settings.conversation_summary_prompt,
            "timeout_ms": self.settings.conversation_summary_timeout_ms,
            "max_recent_messages": self.settings.conversation_summary_max_recent_messages,
        }

    def _get_rename_settings(self) -> dict[str, Any]:
        return {
            "system_prompt": self.settings.conversation_auto_rename_prompt,
            "timeout_ms": self.settings.conversation_auto_rename_timeout_ms,
        }

    async def generate_summary(
        self,
        conversation: Conversation,
        *,
        timeout_ms: int | None = None,
        current_user_id: str,
    ) -> dict[str, Any]:
        """Produce or refresh the summary for a conversation."""
        meta = dict(conversation.meta or {})
        previous_last_id = meta.get("summary_last_message_id")
        previous_summary = conversation.summary_text or ""

        summary_settings = self._get_summary_settings()
        system_prompt = summary_settings["system_prompt"]
        default_timeout = summary_settings["timeout_ms"]
        max_recent = summary_settings["max_recent_messages"]

        # Get the last N messages. They are reversed so we move them into chronological order again.
        messages = await self.chat_service.get_conversation_messages(
            conversation_id=conversation.id, limit=max_recent, order_desc=True
        )
        messages = list(reversed(messages))
        messages = collapse_assistant_variants(messages, previous_last_id)
        last_message_id = messages[-1].id if messages else None

        # If nothing new, skip expensive call
        if last_message_id is None or last_message_id == previous_last_id:
            logger.info(
                "Skipping summary refresh for conversation %s (no new messages)",
                conversation.id,
            )
            return {
                "summary": previous_summary or "",
                "last_message_id": previous_last_id,
                "was_updated": False,
                "tokens_used": 0,
                "response_time_ms": None,
                "model_config_id": None,
            }

        # Determine which messages to send (limit size). Always include at least recent ones.
        message_sequence, newest_message_id = self._prepare_summary_messages(
            messages,
            max_recent=max_recent,
        )

        if not message_sequence:
            return {
                "summary": previous_summary or "",
                "last_message_id": previous_last_id,
                "was_updated": False,
                "tokens_used": 0,
                "response_time_ms": None,
                "model_config_id": None,
            }

        message_sequence = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"PREVIOUS_SUMMARY:\n\n{previous_summary}"},
                    {
                        "type": "input_text",
                        "text": f"NEW_MESSAGES (JSON):\n\n{json.dumps(message_sequence)}",
                    },
                    {
                        "type": "input_text",
                        "text": "TASK: Produce the NEW_SUMMARY as bullet points only.",
                    },
                ],
            }
        ]

        effective_timeout = timeout_ms or default_timeout
        side_call_result = await self.side_call_service.call(
            message_sequence=message_sequence,
            system_prompt=system_prompt,
            user_id=current_user_id,
            timeout_ms=effective_timeout,
        )

        if not side_call_result.success:
            raise RuntimeError(f"Conversation summary side-call failed: {side_call_result.error_message}")

        summary_text = side_call_result.content.strip()

        meta["summary_last_message_id"] = newest_message_id
        conversation.meta = meta
        conversation.summary_text = summary_text
        conversation.updated_at = datetime.now(UTC)

        await self.db_session.commit()
        await self.db_session.refresh(conversation)

        return {
            "summary": summary_text,
            "last_message_id": newest_message_id,
            "was_updated": True,
            "tokens_used": side_call_result.tokens_used,
            "response_time_ms": side_call_result.response_time_ms,
            "model_config_id": side_call_result.metadata.get("model_config_id") if side_call_result.metadata else None,
        }

    async def auto_rename(
        self,
        conversation: Conversation,
        *,
        timeout_ms: int | None = None,
        current_user_id: str,
        fallback_user_message: str | None = None,
    ) -> dict[str, Any]:
        """Propose and apply an automated conversation title."""
        meta = dict(conversation.meta or {})
        title_locked = bool(meta.get("title_locked"))
        if title_locked:
            return {
                "title": conversation.title or "",
                "was_renamed": False,
                "title_locked": True,
                "reason": "TITLE_LOCKED",
                "last_message_id": meta.get("last_auto_title_message_id"),
                "tokens_used": 0,
                "response_time_ms": None,
                "model_config_id": None,
            }

        # Get the last message in the line
        messages = await self.chat_service.get_conversation_messages(
            conversation_id=conversation.id, limit=1, order_desc=True
        )
        last_message = messages[0] if messages else None
        last_message_id = last_message.id if last_message else None

        rename_settings = self._get_rename_settings()
        system_prompt = rename_settings["system_prompt"]
        default_timeout = rename_settings["timeout_ms"]

        summary_source: str | None = getattr(conversation, "summary_text", None)
        if not summary_source:
            if last_message:
                summary_source = last_message.content
            elif fallback_user_message:
                summary_source = fallback_user_message
        if not summary_source:
            return {
                "title": "New Chat",
                "was_renamed": False,
                "title_locked": False,
                "reason": "NO_DATA",
                "last_message_id": last_message_id,
                "tokens_used": 0,
                "response_time_ms": None,
                "model_config_id": None,
            }

        message_sequence = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"SUMMARY:\n\n{summary_source or ''}"},
                    {
                        "type": "input_text",
                        "text": "TASK: Produce the CHAT_NAME as it relates to the chat SUMMARY.",
                    },
                ],
            }
        ]

        effective_timeout = timeout_ms or default_timeout
        side_call_result = await self.side_call_service.call(
            message_sequence=message_sequence,
            system_prompt=system_prompt,
            user_id=current_user_id,
            timeout_ms=effective_timeout,
        )

        if not side_call_result.success:
            raise RuntimeError(f"Conversation rename side-call failed: {side_call_result.error_message}")

        # The field itself only supports 200 chars, so let's force it if the LLM didn't
        proposed_title = side_call_result.content.strip()[:200]

        # Skip if identical to current title
        if proposed_title and proposed_title != (conversation.title or ""):
            conversation.title = proposed_title
            meta["last_auto_title_message_id"] = last_message_id
            conversation.meta = meta
            conversation.updated_at = datetime.now(UTC)
            await self.db_session.commit()
            await self.db_session.refresh(conversation)
            renamed = True
        else:
            renamed = False
            if last_message_id:
                meta["last_auto_title_message_id"] = last_message_id
                conversation.meta = meta
                await self.db_session.commit()
                await self.db_session.refresh(conversation)

        return {
            "title": conversation.title or "",
            "was_renamed": renamed,
            "title_locked": False,
            "reason": None if renamed else "UNCHANGED",
            "last_message_id": last_message_id,
            "tokens_used": side_call_result.tokens_used,
            "response_time_ms": side_call_result.response_time_ms,
            "model_config_id": side_call_result.metadata.get("model_config_id") if side_call_result.metadata else None,
        }

    def _prepare_summary_messages(
        self,
        messages: list[Message],
        *,
        max_recent: int,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Prepare a limited chat sequence for summarization calls."""
        if not messages:
            return [], None

        selected = messages[-max_recent:] if max_recent and len(messages) > max_recent else messages

        sequence: list[dict[str, str]] = []
        for msg in selected:
            content = (msg.content or "").strip()
            sequence.append({"role": msg.role, "content": content})

        newest_id = selected[-1].id if selected else None
        return sequence, newest_id
