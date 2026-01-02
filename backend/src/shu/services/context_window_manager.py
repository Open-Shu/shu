import logging
from typing import Callable, List, Optional, TYPE_CHECKING

from shu.models.llm_provider import Conversation
from .chat_types import ChatMessage

if TYPE_CHECKING:
    from .side_call_service import SideCallService

logger = logging.getLogger(__name__)

class ContextWindowManager:
    """
    Helper that encapsulates the context-window logic previously inside ChatService.
    """

    def __init__(
        self,
        llm_service,
        db_session,
        config_manager,
        *,
        side_call_service: Optional["SideCallService"] = None,
        recent_message_limit: int = 10,
        summary_prompt: str = (
            "Please provide a concise summary of the following conversation, focusing on key topics, "
            "decisions, and important information that would be useful for continuing the conversation:\n\n"
            "{conversation_text}\n\nSummary:"
        ),
        token_estimator: Optional[Callable[[str], int]] = None,
    ):
        self.llm_service = llm_service
        self.db_session = db_session
        self.config_manager = config_manager
        self.side_call_service = side_call_service
        self.recent_message_limit = recent_message_limit
        self.summary_prompt = summary_prompt
        self._token_estimator_override = token_estimator

    async def manage_context_window(
        self,
        messages: List[ChatMessage],
        *,
        conversation: Conversation,
        max_tokens: int,
        recent_message_limit_override: Optional[int] = None,
    ) -> List[ChatMessage]:
        """
        Apply pruning/summarization to the message list.
        """

        def get_content_text(msg) -> str:
            """Extract text from message content, handling multimodal formats."""
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Multimodal content - extract text from text parts
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif "text" in part:
                            text_parts.append(part.get("text", ""))
                return " ".join(text_parts)
            return ""

        def estimate_tokens(text: str) -> int:
            if self._token_estimator_override:
                return self._token_estimator_override(text)
            return int(len(text.split()) * 1.3)

        total_tokens = sum(estimate_tokens(get_content_text(msg)) for msg in messages)
        if total_tokens <= max_tokens:
            return messages

        logger.info(
            "Context window management needed: %s tokens > %s limit",
            total_tokens,
            max_tokens,
        )

        limit = recent_message_limit_override or self.recent_message_limit
        limit = max(1, limit)
        recent_messages = messages[-limit:]
        older_messages = messages[:-limit]

        managed_messages: List[ChatMessage] = []

        if older_messages:
            summary = await self._summarize_conversation_history(older_messages)
            if summary:
                # Use 'user' role instead of 'system' to avoid adapter compatibility issues.
                # Adapters like Anthropic/Gemini expect system content via ChatContext.system_prompt,
                # not as messages in the array. Prefixing clearly marks this as context.
                managed_messages.append(
                    ChatMessage(id=None, role="user", content=f"[Previous conversation summary]: {summary}", created_at=None, attachments=[], metadata={"is_context_summary": True})
                )

        managed_messages.extend(recent_messages)

        final_tokens = sum(estimate_tokens(get_content_text(msg)) for msg in managed_messages)
        logger.info(
            "Context window managed: %s -> %s tokens",
            total_tokens,
            final_tokens,
        )

        return managed_messages

    async def _summarize_conversation_history(
        self,
        messages: List[ChatMessage],
    ) -> Optional[str]:
        if not messages:
            return None

        try:
            conversation_text = "\n".join(
                [
                    f"{getattr(msg, 'role', '').title()}: {getattr(msg, 'content', '')}"
                    for msg in messages
                ]
            )

            if "{conversation_text}" in self.summary_prompt:
                summary_prompt = self.summary_prompt.replace(
                    "{conversation_text}", conversation_text
                )
            else:
                summary_prompt = f"{self.summary_prompt}\n\n{conversation_text}\n\nSummary:"

            # Use the side-call service if available (preferred path)
            if self.side_call_service:
                result = await self.side_call_service.call(
                    message_sequence=[{"role": "user", "content": summary_prompt}],
                    system_prompt=None,
                    user_id="system",
                )
                if result.success:
                    logger.info("Generated conversation summary via side-call service")
                    return (result.content or "").strip()
                else:
                    logger.warning(
                        "Side-call service summarization failed: %s",
                        result.error_message
                    )
                    return None

            # Fallback: no side-call service configured
            logger.warning(
                "No side-call service configured for context summarization; "
                "skipping summarization to avoid using arbitrary provider"
            )
            return None

        except Exception as exc:
            logger.warning("Failed to summarize conversation history: %s", exc)
            return None
